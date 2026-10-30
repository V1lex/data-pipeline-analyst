"""Catches gate bypasses, lost version context and invalid model output acceptance."""

import json
from uuid import UUID

import pytest

from pipeline_generator.errors import PipelineError
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.models import Completion, VerifiedContext


def context(**updates):
    data = {
        "spec_id": "00000000-0000-0000-0000-000000000001",
        "spec_version": 2,
        "verification_run_id": "00000000-0000-0000-0000-000000000002",
        "approved_requirements": "Read readings(sensor, value); average value by sensor; save Parquet.",
        "messages": [{"role": "user", "content": "Use mean, not sum."}],
        "behavioral_model": {
            "variables": [{"name": "done", "type": "boolean"}],
            "initial": {"done": False},
            "transitions": [{"name": "Finish", "effects": [{"set": ["done", True]}]}],
            "properties": [
                {"id": "P1", "type": "reachable", "expression": {"eq": [{"var": "done"}, True]}}
            ],
        },
        "verification_result": {
            "status": "PROPERTY_HOLDS",
            "engine": "TLC",
            "statistics": {"states_generated": 2, "distinct_states": 2, "depth": 1},
        },
        "target_environment": {"pyspark_version": "3.5.8", "extra_dependencies": []},
    }
    data.update(updates)
    return VerifiedContext.model_validate(data)


def response(**updates):
    data = {
        "status": "GENERATED",
        "filename": "sensor_mean.py",
        "content": "from pyspark.sql import SparkSession\n",
        "requirements": ["pyspark==3.5.8"],
        "notes": [],
        "questions": [],
    }
    data.update(updates)
    return json.dumps(data)


class FakeLlm:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, system, user, response_schema):
        self.calls.append((system, json.loads(user), response_schema))
        item = next(self.responses)
        if isinstance(item, Exception):
            raise item
        return item


def completion(text=None, finish_reason="STOP"):
    return Completion(
        text=response() if text is None else text,
        finish_reason=finish_reason,
        provider="fake",
        model="test-only",
    )


def test_success_retains_exact_context_and_disclaimer():
    client = FakeLlm(completion())
    result = PySparkGenerator(client).generate(context())
    assert result.filename == "sensor_mean.py"
    assert result.code_validation == "not_performed"
    assert result.provider == "fake"
    assert any("не проверялась" in note for note in result.notes)
    sent = client.calls[0][1]
    assert sent["spec_version"] == 2
    assert sent["messages"] == [{"role": "user", "content": "Use mean, not sum."}]
    assert sent["approved_requirements"].startswith("Read readings")
    assert sent["verification_run_id"] == str(UUID(int=2))


@pytest.mark.parametrize(
    "status",
    [
        "MODEL_INVALID",
        "PROPERTY_VIOLATED",
        "TIMEOUT",
        "STATE_LIMIT_REACHED",
        "ENGINE_ERROR",
        "INFRASTRUCTURE_ERROR",
        "success",
        "UNKNOWN",
    ],
)
def test_only_overall_property_holds_can_generate(status):
    client = FakeLlm(completion())
    with pytest.raises(PipelineError) as error:
        PySparkGenerator(client).generate(
            context(verification_result={"status": status, "engine": "TLC"})
        )
    assert error.value.code == "VERIFICATION_REQUIRED"
    assert client.calls == []


@pytest.mark.parametrize(
    "updates",
    [
        {"approved_requirements": " "},
        {"behavioral_model": {}},
        {"verification_result": {"status": "PROPERTY_HOLDS", "engine": "other"}},
        {"verification_result": {"status": "PROPERTY_HOLDS", "engine": "TLC"}},
    ],
)
def test_incomplete_or_untrusted_context_is_rejected(updates):
    client = FakeLlm(completion())
    with pytest.raises(PipelineError):
        PySparkGenerator(client).generate(context(**updates))
    assert client.calls == []


@pytest.mark.parametrize(
    ("text", "finish", "code"),
    [
        (response(), "MAX_TOKENS", "LLM_TRUNCATED"),
        (response(), "SAFETY", "LLM_BLOCKED"),
        (response(), "UNKNOWN", "LLM_BLOCKED"),
        ("not json", "STOP", "LLM_INVALID_RESPONSE"),
        (response(content=" \n"), "STOP", "EMPTY_CODE"),
        (response(filename="../../secret.py"), "STOP", "LLM_INVALID_RESPONSE"),
        (response(filename="evil\r\n.py"), "STOP", "LLM_INVALID_RESPONSE"),
        (response(content="x", requirements="pyspark"), "STOP", "LLM_INVALID_RESPONSE"),
        (response(unexpected="extra"), "STOP", "LLM_INVALID_RESPONSE"),
    ],
)
def test_invalid_responses_never_become_ready_code(text, finish, code):
    with pytest.raises(PipelineError) as error:
        PySparkGenerator(FakeLlm(completion(text, finish))).generate(context())
    assert error.value.code == code


def test_clarification_is_returned_without_inventing_code():
    with pytest.raises(PipelineError) as error:
        PySparkGenerator(
            FakeLlm(
                completion(
                    response(
                        status="NEEDS_CLARIFICATION",
                        filename="",
                        content="",
                        questions=["Which null policy?"],
                    )
                )
            )
        ).generate(context())
    assert error.value.code == "NEEDS_CLARIFICATION"
    assert error.value.details == {"questions": ["Which null policy?"]}


def test_transient_errors_are_bounded_and_can_recover():
    client = FakeLlm(
        PipelineError("LLM_TRANSIENT_ERROR", "Retry later", retryable=True), completion()
    )
    assert (
        PySparkGenerator(client, max_attempts=2, retry_delay=0).generate(context()).filename
        == "sensor_mean.py"
    )
    assert len(client.calls) == 2


def test_auth_and_invalid_output_are_not_retried():
    client = FakeLlm(PipelineError("LLM_AUTH_ERROR", "Check key"), completion())
    with pytest.raises(PipelineError) as error:
        PySparkGenerator(client, max_attempts=2, retry_delay=0).generate(context())
    assert error.value.code == "LLM_AUTH_ERROR"
    assert len(client.calls) == 1
