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
    assert payload["messages"][-1] == {"role": "user", "content": "Привет"}
    assert requests[0].url == "https://polza.ai/api/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-key"


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
