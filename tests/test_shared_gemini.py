from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

pytest.importorskip("google.genai")
from google.genai import errors, types

from pipeline_generator.errors import PipelineError
from pipeline_generator.models import ModelPayload
from pipeline_generator.shared_gemini import SharedGeminiClient


def client_and_adapter():
    raw = SimpleNamespace(models=SimpleNamespace(generate_content=Mock()))
    analyst = SimpleNamespace(client=raw, model_name="team-model")
    return raw, SharedGeminiClient.from_analyst_client(analyst)


def test_reuses_sdk_client_with_own_schema_and_single_attempt():
    raw, adapter = client_and_adapter()
    raw.models.generate_content.return_value = types.GenerateContentResponse(
        model_version="team-model-version",
        candidates=[
            types.Candidate(
                finish_reason="STOP",
                content=types.Content(
                    parts=[
                        types.Part(text="hidden", thought=True),
                        types.Part(text='{"answer":"reply"}'),
                    ]
                ),
            )
        ],
    )
    result = adapter.complete("system", "user", ModelPayload.model_json_schema())
    assert result.text == '{"answer":"reply"}'
    assert result.finish_reason == "STOP"
    assert result.model == "team-model-version"
    call = raw.models.generate_content.call_args.kwargs
    assert call["model"] == "team-model"
    assert call["config"].response_json_schema == ModelPayload.model_json_schema()
    assert call["config"].http_options.retry_options.attempts == 1
    assert call["config"].http_options.timeout == 60000


def test_real_sdk_serializes_our_schema_without_a_network_request():
    from google import genai

    seen = []

    def handle(request):
        import json

        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "model output"}]},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with genai.Client(
            api_key="test-only", http_options=types.HttpOptions(httpx_client=http)
        ) as raw:
            result = SharedGeminiClient(raw, "team-model").complete(
                "system",
                "user",
                ModelPayload.model_json_schema(),
            )
    assert result.text == "model output"
    assert seen[0]["generationConfig"]["responseJsonSchema"] == ModelPayload.model_json_schema()
    assert len(seen) == 1


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY"])
def test_preserves_non_success_finish_reason(reason):
    raw, adapter = client_and_adapter()
    raw.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[types.Candidate(finish_reason=reason)],
    )
    assert adapter.complete("s", "u", {}).finish_reason == reason


@pytest.mark.parametrize(
    "exception,code,retryable",
    [
        (errors.ClientError(429, {"error": {"message": "secret"}}), "LLM_TRANSIENT_ERROR", True),
        (errors.ClientError(403, {"error": {"message": "secret"}}), "LLM_AUTH_ERROR", False),
        (
            errors.ClientError(
                400,
                {
                    "error": {
                        "status": "FAILED_PRECONDITION",
                        "message": "User location is not supported for the API use.",
                    }
                },
            ),
            "LLM_ACCESS_ERROR",
            False,
        ),
        (httpx.ReadTimeout("secret"), "LLM_TIMEOUT", True),
    ],
)
def test_provider_errors_do_not_leak_raw_data(exception, code, retryable):
    raw, adapter = client_and_adapter()
    raw.models.generate_content.side_effect = exception
    with pytest.raises(PipelineError) as failure:
        adapter.complete("s", "u", {})
    assert failure.value.code == code
    assert failure.value.retryable is retryable
    assert "secret" not in str(failure.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not json secret",
        b'{"candidates":[{"content":{"parts":[{"text":{"secret":1}}]}}]}',
    ],
)
def test_actual_sdk_bad_response_is_sanitized_and_request_is_failed(repository, body):
    import json
    from uuid import uuid4

    from google import genai
    from test_storage import verified

    from pipeline_generator.generator import PySparkGenerator
    from pipeline_generator.service import GenerationService

    _, spec, _ = verified(repository)
    request_id = uuid4()
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    ) as http:
        with genai.Client(
            api_key="test-only", http_options=types.HttpOptions(httpx_client=http)
        ) as raw:
            service = GenerationService(
                repository, PySparkGenerator(SharedGeminiClient(raw, "team-model"))
            )
            with pytest.raises(PipelineError) as failure:
                service.generate(spec, request_id=request_id)
    assert failure.value.code == "LLM_INVALID_RESPONSE"
    assert "secret" not in str(failure.value)
    with repository.connect() as connection:
        manifests = connection.execute(
            "SELECT content FROM artifacts WHERE spec_id=%s AND kind='generation_manifest'", (spec,)
        ).fetchall()
    assert len(manifests) == 1
    assert json.loads(manifests[0]["content"])["state"] == "FAILED"
