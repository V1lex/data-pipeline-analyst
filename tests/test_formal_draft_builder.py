import json

import pytest


def test_formal_builder_keeps_approved_pipeline_and_repairs_invalid_formal_model():
    from agent.analyst.specification_builder import MockSpecificationBuilder, SpecificationBuilder
    from pipeline_spec.models import PipelineSpecification

    reference = MockSpecificationBuilder().build_verifiable_specification([])
    original = reference.model_dump(mode="json", by_alias=True)
    formal = original.pop("formal_model")
    approved = PipelineSpecification.model_validate(original)
    replies = [
        json.dumps({"formal_model": {"variables": []}}),
        json.dumps({"formal_model": formal, "constraints": [{"made_up": True}]}),
    ]

    class Client:
        def generate_json(self, system, prompt):
            return replies.pop(0)

    builder = SpecificationBuilder(client=Client())
    result = builder.build_from_approved_draft(approved, [])
    actual = result.model_dump(mode="json", by_alias=True)
    assert actual["sources"] == original["sources"]
    assert actual["steps"] == original["steps"]
    assert actual["sinks"] == original["sinks"]
    assert actual["constraints"] == original["constraints"]
    assert actual["formal_model"] == formal
    assert replies == []


def test_formal_builder_fails_safely_if_model_never_returns_valid_model():
    from agent.analyst.specification_builder import MockSpecificationBuilder, SpecificationBuilder
    from pipeline_generator.errors import PipelineError

    approved = MockSpecificationBuilder().build_verifiable_specification([])

    class Client:
        def generate_json(self, system, prompt):
            return '{"formal_model": {"variables": []}}'

    with pytest.raises(PipelineError, match="LLM_INVALID_RESPONSE"):
        SpecificationBuilder(client=Client(), max_retries=2).build_from_approved_draft(approved, [])
