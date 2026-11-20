from uuid import uuid4

import pytest
from pipeline_generator.errors import PipelineError
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.service import GenerationService
from test_api import CodeModel
from test_generator import context

pytestmark = pytest.mark.postgres


def prepared(repository):
    project = repository.create_project("execution")
    source = repository.store_source_file(
        project,
        "data.csv",
        b"city,amount\nMoscow,10\n",
        {"format": "csv", "columns": [{"name": "city"}, {"name": "amount"}]},
        max_files=4,
    )
    repository.select_source_files(project, [source])
    spec = repository.create_specification(
        project,
        context().behavioral_model,
        "requirements",
        [],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    repository.approve_specification(spec)
    run = repository.enqueue_verification(spec)
    attempt = repository.claim_verification()
    repository.finish_verification(
        run,
        {
            "status": "PROPERTY_HOLDS",
            "engine": "TLC",
            "statistics": {"states_generated": 1, "distinct_states": 1, "depth": 1},
        },
        attempt["lease_token"],
    )
    generated = GenerationService(repository, PySparkGenerator(CodeModel())).generate(spec, uuid4())
    return project, spec, source, generated.artifact_id


def test_execution_is_bound_to_verified_artifact_and_selected_source(repository):
    from execution.storage import ExecutionStore

    _, spec, source, artifact = prepared(repository)
    store = ExecutionStore(repository)
    request = uuid4()
    assert store.enqueue(spec, artifact, {"--orders": source}, request) == request
    assert store.enqueue(spec, artifact, {"--orders": source}, request) == request
    with pytest.raises(PipelineError):
        store.enqueue(spec, artifact, {"--orders": uuid4()}, uuid4())
    with pytest.raises(PipelineError):
        store.enqueue(spec, uuid4(), {"--orders": source}, uuid4())
    run = store.claim()
    assert run["id"] == request and run["status"] == "RUNNING"


def test_execution_requires_matching_latest_tlc_and_rejects_stale_worker(repository):
    from execution.storage import ExecutionStore

    _, spec, source, artifact = prepared(repository)
    store = ExecutionStore(repository)
    job = store.enqueue(spec, artifact, {"--orders": source}, uuid4())
    old = store.claim(lease_seconds=1)
    with repository.connect() as connection:
        connection.execute(
            "UPDATE execution_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
            (job,),
        )
    current = store.claim()
    with pytest.raises(PipelineError, match="LEASE_CONFLICT"):
        store.finish(job, old["lease_token"], "FAILED", error="old")
    store.finish(job, current["lease_token"], "SUCCEEDED", archive=b"zip", preview={"rows": []})
    assert store.get(spec, job)["status"] == "SUCCEEDED"
    repository.enqueue_verification(spec)
    with pytest.raises(PipelineError, match="VERIFICATION_REQUIRED"):
        store.enqueue(spec, artifact, {"--orders": source}, uuid4())


def test_worker_executes_only_saved_artifact_and_version_sources(repository):
    from execution.storage import ExecutionStore
    from execution.worker import process_one

    _, spec, source, artifact = prepared(repository)
    store = ExecutionStore(repository)
    request = store.enqueue(spec, artifact, {"--orders": source}, uuid4())
    seen = {}

    def run(code, sources, mapping, **kwargs):
        seen.update(code=code, sources=sources, mapping=mapping)
        return {"status": "SUCCEEDED", "archive": b"archive"}

    assert process_one(repository, runner=run, inspect_result=lambda _: ({"rows": []}, b"csv"))
    assert seen["code"] == "print(1)\n"
    assert set(seen["sources"]) == {source}
    assert seen["mapping"] == {"--orders": source}
    assert store.get(spec, request)["status"] == "SUCCEEDED"
    saved = store.get(spec, request, include_archive=True)
    assert saved["result_archive"] == b"archive" and saved["result_csv"] == b"csv"


def test_execution_parameters_are_saved_and_idempotency_checked(repository):
    from execution.storage import ExecutionStore

    _, spec, source, artifact = prepared(repository)
    store = ExecutionStore(repository)
    request_id = uuid4()
    store.enqueue(spec, artifact, {"--orders": source}, request_id, {"--threshold": "12"})
    assert store.get(spec, request_id)["parameters"] == {"--threshold": "12"}
    with pytest.raises(PipelineError, match="REQUEST_CONFLICT"):
        store.enqueue(spec, artifact, {"--orders": source}, request_id, {"--threshold": "13"})
    with pytest.raises(PipelineError, match="EXECUTION_INPUT_INVALID"):
        store.enqueue(spec, artifact, {"--orders": source}, uuid4(), {"--output": "/tmp"})
