"""Run against the actual team branches on PYTHONPATH; no copied contract classes."""

import json
from pathlib import Path

import pytest
from test_generator import context

from pipeline_generator.errors import PipelineError


def test_legacy_context_hash_stays_stable():
    assert (
        context().fingerprint()
        == "ce4d4c2842e0ab478cfcbae8ba67fa187db7ac4f8c7aa2cd89137dbfbcbb03a4"
    )


def team_specification():
    module = pytest.importorskip("pipeline_spec.models")
    package = pytest.importorskip("pipeline_spec")
    example = Path(package.__file__).parent / "examples/e_commerce_orders_pipeline.json"
    data = json.loads(example.read_text())
    data["formal_model"] = context().behavioral_model
    return module.PipelineSpecification.model_validate(data)


def test_real_pipeline_and_checker_round_trip(repository):
    spec = team_specification()
    analyst = pytest.importorskip("analyst.models")
    output = pytest.importorskip("src.models.output")
    project = repository.create_project("Team contract test")
    reply = analyst.ChatMessage(role=analyst.Role.MODEL, content="Уточните схему")
    message = repository.add_message(project, reply.role, reply.content)
    assert repository.list_messages(project)[0]["role"] == "assistant"
    version = repository.create_specification(
        project,
        spec,
        "Confirmed test requirements",
        [message],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    run = repository.enqueue_verification(version)
    # Synthetic result object tests transport, not a real TLC execution.
    repository.finish_verification(
        run,
        output.PropertyHolds(
            statistics=output.Statistics(states_generated=2, distinct_states=2, depth=1),
        ),
    )
    with pytest.raises(PipelineError) as failure:
        repository.load_verified_context(version)
    assert failure.value.code == "VERIFICATION_REQUIRED"
    repository.approve_specification(version)
    loaded = repository.load_verified_context(version)
    assert loaded.spec_version == 1
    assert loaded.pipeline_specification["version"] == "1.0.0"
    assert (
        loaded.pipeline_specification["steps"]
        == spec.model_dump(mode="json", by_alias=True)["steps"]
    )
    assert loaded.behavioral_model == spec.formal_model.to_formal_check_input().model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert loaded.pipeline_specification["sources"][0]["schema"]


def test_real_checker_json_is_accepted_without_changing_verdict(repository):
    output = pytest.importorskip("src.models.output")
    from test_storage import approved

    _, spec = approved(repository)
    run = repository.enqueue_verification(spec)
    result = output.ModelInvalid(
        errors=[
            output.ValidationErrorItem(
                type="invalid",
                location="properties",
                message="Synthetic invalid result",
            )
        ]
    )
    repository.finish_verification(run, result.model_dump_json())
    with pytest.raises(PipelineError) as failure:
        repository.load_verified_context(spec)
    assert failure.value.code == "VERIFICATION_REQUIRED"


def test_pipeline_without_formal_model_is_not_treated_as_verified(repository):
    spec = team_specification().model_copy(update={"formal_model": None})
    project = repository.create_project("Missing model")
    with pytest.raises(PipelineError) as failure:
        repository.create_specification(
            project, spec, "requirements", [], {"pyspark_version": "3.5.8"}
        )
    assert failure.value.code == "CONTEXT_REQUIRED"


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "with_columns", "columns": [{"name": "missing", "expr": {"lit": None}}]},
        {"type": "with_columns", "columns": [{"name": "missing", "expr": None}]},
        {"type": "deduplicate", "keep": None},
    ],
)
def test_full_pipeline_preserves_semantic_null_values(operation):
    from pipeline_generator.team_contracts import specification_snapshot

    spec = team_specification()
    module = pytest.importorskip("pipeline_spec.models")
    data = spec.model_dump(mode="json", by_alias=True)
    data["steps"][-1]["operation"] = operation
    spec = module.PipelineSpecification.model_validate(data)
    _, snapshot = specification_snapshot(spec)
    round_trip = module.PipelineSpecification.model_validate(snapshot)
    assert round_trip.model_dump(mode="json", by_alias=True) == spec.model_dump(
        mode="json", by_alias=True
    )
