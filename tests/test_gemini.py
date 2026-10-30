import json

import httpx
import pytest

from pipeline_generator.errors import PipelineError
from pipeline_generator.gemini import GeminiClient


def test_official_request_shape_and_model_finish_reason():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"thought": True, "text": "private thought"},
                                {"text": '{"status":"GENERATED"}'},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": "gemini-3.1-flash-lite",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        result = GeminiClient(
            api_key="test-secret", model="gemini-3.1-flash-lite", http=http
        ).complete("system", "user", {"type": "object"})
    request = seen[0]
    assert (
        str(request.url)
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
    )
    assert "test-secret" not in str(request.url)
    assert request.headers["x-goog-api-key"] == "test-secret"
    body = json.loads(request.content)
    assert body["systemInstruction"]["parts"] == [{"text": "system"}]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "user"}]}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert result.text == '{"status":"GENERATED"}'
    assert result.finish_reason == "STOP"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (400, "CONFIGURATION_ERROR", False),
        (401, "LLM_AUTH_ERROR", False),
        (403, "LLM_AUTH_ERROR", False),
        (404, "CONFIGURATION_ERROR", False),
        (429, "LLM_TRANSIENT_ERROR", True),
        (503, "LLM_TRANSIENT_ERROR", True),
    ],
)
def test_http_errors_are_sanitized(status, code, retryable):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret upstream body test-secret")
        )
    ) as http:
        client = GeminiClient(api_key="test-secret", model="gemini-3.1-flash-lite", http=http)
        with pytest.raises(PipelineError) as error:
            client.complete("system", "user", {})
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": []},
        {
            "candidates": [
                {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "partial"}]}}
            ]
        },
    ],
)
def test_unfinished_or_blocked_outputs_preserve_non_stop_reason(payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as http:
        result = GeminiClient(api_key="test", model="gemini-3.1-flash-lite", http=http).complete(
            "s", "u", {}
        )
    assert result.finish_reason != "STOP"


def test_missing_key_is_configuration_error():
    with pytest.raises(PipelineError) as error:
        GeminiClient(api_key="", model="gemini-3.1-flash-lite")
    assert error.value.code == "CONFIGURATION_ERROR"


def test_timeout_is_retryable_without_disclosing_request():
    def timeout(request):
        raise httpx.ReadTimeout("secret request", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as http:
        with pytest.raises(PipelineError) as error:
            GeminiClient(api_key="test", model="gemini-3.1-flash-lite", http=http).complete(
                "s", "u", {}
            )
    assert error.value.code == "LLM_TIMEOUT"
    assert error.value.retryable


def test_region_precondition_is_access_error_not_an_invalid_key():
    payload = {
        "error": {
            "status": "FAILED_PRECONDITION",
            "message": "User location is not supported for the API use.",
        }
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json=payload))
    ) as http:
        with pytest.raises(PipelineError) as error:
            GeminiClient(api_key="test", model="gemini-3.1-flash-lite", http=http).complete(
                "s", "u", {}
            )
    assert error.value.code == "LLM_ACCESS_ERROR"
    assert not error.value.retryable
    assert "регион" in error.value.message


def test_explicit_env_file_loads_key_without_overriding_process_environment(tmp_path, monkeypatch):
    config = tmp_path / ".env"
    config.write_text(
        'GEMINI_API_KEY="test-file-key"\nGEMINI_MODEL=gemini-3.1-flash-lite\nLLM_MAX_OUTPUT_TOKENS=1024\n'
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MAX_OUTPUT_TOKENS", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    client = GeminiClient.from_environment(env_file=config)
    try:
        assert client.model == "gemini-3.5-flash-lite"
        assert client.max_output_tokens == 1024
        assert "test-file-key" not in repr(client)
    finally:
        client.close()
