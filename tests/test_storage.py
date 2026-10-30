"""Real PostgreSQL tests: no SQLite or fake persistence."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from psycopg import errors
from test_generator import context

from pipeline_generator.errors import PipelineError
from pipeline_generator.storage import PostgresRepository

pytestmark = pytest.mark.postgres

HOLDS = {
    "status": "PROPERTY_HOLDS",
    "engine": "TLC",
    "statistics": {"states_generated": 2, "distinct_states": 2, "depth": 1},
}


def approved(repository):
    project = repository.create_project("Synthetic readings", "Only test data")
    message = repository.add_message(project, "user", "Use mean, not sum.")
    spec = repository.create_specification(
        project,
        context().behavioral_model,
        "Read readings(sensor,value); average by sensor; save Parquet.",
        [message],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    repository.approve_specification(spec)
    return project, spec


def verified(repository):
    project, spec = approved(repository)
    run = repository.enqueue_verification(spec)
    repository.finish_verification(run, HOLDS)
    return project, spec, run


def test_data_and_version_snapshot_survive_new_repository(repository):
    project, spec, run = verified(repository)
    repository.add_message(project, "user", "Later unapproved change: use sum.")
    reopened = PostgresRepository(repository.dsn, schema=repository.schema)
    loaded = reopened.load_verified_context(spec)
    assert reopened.get_project(project)["name"] == "Synthetic readings"
    assert loaded.spec_version == 1
    assert loaded.verification_run_id == run
    assert [m["content"] for m in loaded.messages] == ["Use mean, not sum."]
    assert len(reopened.list_messages(project)) == 2


def test_new_version_does_not_inherit_old_verification(repository):
    project, spec, _ = verified(repository)
    new_spec = repository.create_specification(
        project,
        context().behavioral_model,
        "New confirmed requirement",
        [],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    repository.approve_specification(new_spec)
    assert [item["version"] for item in repository.list_specifications(project)] == [1, 2]
    with pytest.raises(PipelineError) as error:
        repository.load_verified_context(new_spec)
    assert error.value.code == "VERIFICATION_REQUIRED"
    assert repository.load_verified_context(spec).spec_version == 1


def test_database_itself_rejects_model_and_context_overwrite(repository):
    _, spec = approved(repository)
    with pytest.raises(errors.RaiseException):
        with repository.connect() as connection:
            connection.execute(
                "UPDATE specifications SET spec_json='{}'::jsonb WHERE id=%s", (spec,)
            )
    with pytest.raises(errors.RaiseException):
        with repository.connect() as connection:
            connection.execute(
                "UPDATE specifications SET context_json='{}'::jsonb WHERE id=%s", (spec,)
            )


def test_messages_from_another_project_cannot_enter_snapshot(repository):
    project = repository.create_project("one")
    other = repository.create_project("two")
    wrong_message = repository.add_message(other, "user", "Not this project's context")
    with pytest.raises(PipelineError) as error:
        repository.create_specification(
            project,
            context().behavioral_model,
            "requirements",
            [wrong_message],
            {"pyspark_version": "3.5.8"},
        )
    assert error.value.code == "CONTEXT_REQUIRED"
    assert repository.list_specifications(project) == []


@pytest.mark.parametrize(
    "result",
    [
        {"status": "MODEL_INVALID", "errors": [{"type": "BAD_MODEL", "message": "invalid"}]},
        {"status": "TIMEOUT", "engine": "TLC", "timeout_limit_seconds": 5.0},
        {"status": "STATE_LIMIT_REACHED", "engine": "TLC", "state_limit": 100},
        {"status": "ENGINE_ERROR", "engine": "TLC", "error": "engine failed"},
        {"status": "INFRASTRUCTURE_ERROR", "engine": "TLC", "error": "infrastructure failed"},
    ],
)
def test_unestablished_results_preserve_raw_diagnostics_without_property_rows(repository, result):
    _, spec, _ = verified(repository)
    run = repository.enqueue_verification(spec)
    repository.finish_verification(run, result)
    assert repository.list_verifications(spec)[-1]["result_json"] == result
    assert repository.list_property_results(run) == []
    with pytest.raises(PipelineError) as error:
        repository.load_verified_context(spec)
    assert error.value.code == "VERIFICATION_REQUIRED"


def test_only_reported_violated_property_and_trace_are_stored(repository):
    _, spec = approved(repository)
    run = repository.enqueue_verification(spec)
    trace = {"steps": [{"number": 0, "transition": None, "state": {"done": False}}]}
    result = {
        "status": "PROPERTY_VIOLATED",
        "engine": "TLC",
        "property_id": "P1",
        "counterexample": trace,
    }
    repository.finish_verification(run, result)
    rows = repository.list_property_results(run)
    assert len(rows) == 1
    assert rows[0]["property_id"] == "P1"
    assert rows[0]["counterexample"] == trace


def test_latest_queued_run_blocks_previous_success_and_queue_claim_is_exclusive(repository):
    _, spec, _ = verified(repository)
    run = repository.enqueue_verification(spec)
    with pytest.raises(PipelineError):
        repository.load_verified_context(spec)
    claimed = repository.claim_verification()
    assert claimed["id"] == run
    assert claimed["status"] == "RUNNING"
    assert repository.claim_verification() is None


def test_finished_verification_cannot_be_rewritten(repository):
    _, _, run = verified(repository)
    with pytest.raises(PipelineError) as error:
        repository.finish_verification(run, {"status": "TIMEOUT", "engine": "TLC"})
    assert error.value.code == "REQUEST_CONFLICT"


def test_verified_snapshot_is_available_to_a_fresh_python_process(repository):
    _, spec, _ = verified(repository)
    script = """
import os
from uuid import UUID
from pipeline_generator.storage import PostgresRepository
repo = PostgresRepository(os.environ['TEST_DATABASE_URL'], schema=os.environ['TEST_SCHEMA'])
print(repo.load_verified_context(UUID(os.environ['TEST_SPEC_ID'])).model_dump_json())
"""
    environment = dict(
        os.environ,
        TEST_DATABASE_URL=repository.dsn,
        TEST_SCHEMA=repository.schema,
        TEST_SPEC_ID=str(spec),
    )
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script], env=environment, capture_output=True, text=True, check=True
    )
    saved = json.loads(completed.stdout)
    assert saved["spec_id"] == str(spec)
    assert saved["spec_version"] == 1
    assert saved["messages"][0]["content"] == "Use mean, not sum."
