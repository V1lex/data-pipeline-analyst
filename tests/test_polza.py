import json

import httpx
import pytest
from agent.analyst.models import ChatMessage, Role
from pipeline_generator.errors import PipelineError


def test_polza_supports_dialogue_specification_and_generator():
    from agent.analyst.polza import PolzaLLMClient

    requests = []
    responses = [
        json.dumps({"message": "Уточните источник", "is_complete": False}),
        '{"version":"1.0.0"}',
        '{"status":"GENERATED"}',
    ]

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": responses.pop(0)}, "finish_reason": "stop"}]},
        )

    http = httpx.Client(transport=httpx.MockTransport(respond))
    client = PolzaLLMClient(api_key="test-key", client=http)
    response = client.generate_response([ChatMessage(role=Role.USER, content="Привет")], "system")
    assert response.message == "Уточните источник"
    assert client.generate_json("system", "spec") == '{"version":"1.0.0"}'
    completion = client.complete("system", "code", {})
    assert completion.provider == "polza" and completion.finish_reason == "STOP"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert payload["reasoning"] == {"type": "disabled"}
    assert payload["messages"][-1] == {"role": "user", "content": "Привет"}
    assert requests[0].url == "https://polza.ai/api/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-key"


def test_polza_repairs_invalid_analyst_category_before_returning_response():
    from agent.analyst.polza import PolzaLLMClient

    requests = []
    replies = [
        {
            "message": "Уточните данные",
            "is_complete": False,
            "clarification_questions": [{"category": "data", "question": "Какие данные?"}],
        },
        {
            "message": "Уточните данные",
            "is_complete": False,
            "clarification_questions": [{"category": "sources", "question": "Какие данные?"}],
        },
    ]

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(replies.pop(0))}, "finish_reason": "stop"}
                ]
            },
        )

    client = PolzaLLMClient(
        api_key="test", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    result = client.generate_response([ChatMessage(role=Role.USER, content="Данные")], "system")
    assert result.clarification_questions[0].category == "sources"
    assert len(requests) == 2
    assert "clarification_questions" in requests[1]["messages"][-1]["content"]


def test_polza_repairs_draft_with_string_operation():
    from agent.analyst.polza import PolzaLLMClient

    calls = []

    def respond(request):
        calls.append(request)
        operation = "join" if len(calls) == 1 else {"type": "join"}
        payload = {
            "message": "Уточним",
            "is_complete": False,
            "draft": {
                "sources": [],
                "steps": [{"id": "join", "operation": operation}],
                "sinks": [],
            },
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}]
            },
        )

    client = PolzaLLMClient(
        api_key="test", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    result = client.generate_response([ChatMessage(role=Role.USER, content="Данные")], "system")
    assert result.draft["steps"][0]["operation"] == {"type": "join"}
    assert len(calls) == 2


def test_polza_stops_after_bounded_invalid_responses():
    from agent.analyst.polza import PolzaLLMClient

    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"message":"oops"}'}, "finish_reason": "stop"}]
            },
        )

    client = PolzaLLMClient(
        api_key="test", client=httpx.Client(transport=httpx.MockTransport(respond))
    )
    with pytest.raises(PipelineError) as exc:
        client.generate_response([ChatMessage(role=Role.USER, content="Данные")], "system")
    assert exc.value.code == "LLM_INVALID_RESPONSE"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "status, code, retryable",
    [
        (401, "LLM_AUTH_ERROR", False),
        (402, "LLM_QUOTA_EXCEEDED", False),
        (429, "LLM_TRANSIENT_ERROR", True),
        (500, "LLM_TRANSIENT_ERROR", True),
    ],
)
def test_polza_maps_provider_errors_safely(status, code, retryable):
    from agent.analyst.polza import PolzaLLMClient

    client = PolzaLLMClient(
        api_key="secret",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, text="secret"))
        ),
    )
    with pytest.raises(PipelineError) as exc:
        client.generate_json("system", "prompt")
    assert exc.value.code == code and exc.value.retryable == retryable
    assert "secret" not in str(exc.value)


def test_specification_builder_uses_polza_json_client():
    from agent.analyst.specification_builder import BUILDER_SYSTEM_INSTRUCTION, SpecificationBuilder

    class Client:
        def generate_json(self, system, prompt):
            assert system == BUILDER_SYSTEM_INSTRUCTION
            assert prompt == "spec prompt"
            return '{"version":"1.0.0"}'

    assert (
        SpecificationBuilder(client=Client())._call_llm_json("spec prompt") == '{"version":"1.0.0"}'
    )


def test_polza_missing_key_is_configuration_error():
    from agent.analyst.polza import PolzaLLMClient

    with pytest.raises(ValueError, match="POLZA_API_KEY"):
        PolzaLLMClient(api_key="")


def test_standalone_specification_builder_defaults_to_polza(monkeypatch):
    from agent.analyst.polza import PolzaLLMClient
    from agent.analyst.specification_builder import SpecificationBuilder

    monkeypatch.setenv("POLZA_API_KEY", "test-key")
    builder = SpecificationBuilder()
    try:
        assert isinstance(builder.client, PolzaLLMClient)
        assert builder.client.model_name == "deepseek/deepseek-v4.1-flash-1017"
    finally:
        builder.client.close()
