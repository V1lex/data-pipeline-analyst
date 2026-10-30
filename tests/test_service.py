import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from test_generator import FakeLlm, completion, response
from test_storage import verified

from pipeline_generator.errors import PipelineError
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.service import GenerationService

pytestmark = pytest.mark.postgres


def test_retry_returns_same_persisted_file_and_regeneration_keeps_both(repository):
    _, spec, _ = verified(repository)
    client = FakeLlm(completion(), completion())
    service = GenerationService(repository, PySparkGenerator(client))
    request = uuid4()
    first = service.generate(spec, request)
    again = service.generate(spec, request)
    regenerated = service.generate(spec, uuid4())
    assert first == again
    assert regenerated.artifact_id != first.artifact_id
    assert regenerated.filename != first.filename
    assert len(client.calls) == 2
    assert repository.get_artifact(spec, first.artifact_id).content == first.content
    assert repository.get_artifact(spec, regenerated.artifact_id).content == regenerated.content
    assert first.code_validation == "not_performed"


def test_failed_model_call_does_not_change_verification_or_store_code(repository):
    _, spec, run = verified(repository)
    client = FakeLlm(PipelineError("LLM_AUTH_ERROR", "Check API key"))
    service = GenerationService(repository, PySparkGenerator(client))
    request = uuid4()
    for _ in range(2):
        with pytest.raises(PipelineError) as error:
            service.generate(spec, request)
        assert error.value.code == "LLM_AUTH_ERROR"
    assert len(client.calls) == 1
    assert repository.list_verifications(spec)[0]["id"] == run
    assert repository.list_verifications(spec)[0]["result_json"]["status"] == "PROPERTY_HOLDS"
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM artifacts WHERE kind='pyspark'"
            ).fetchone()["n"]
            == 0
        )


def test_concurrent_same_request_has_one_call_and_no_open_transaction(repository):
    _, spec, _ = verified(repository)
    entered, release = threading.Event(), threading.Event()

    class WaitingClient:
        def complete(self, system, user, response_schema):
            entered.set()
            assert release.wait(timeout=10)
            return completion()

    service = GenerationService(repository, PySparkGenerator(WaitingClient()))
    request = uuid4()
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(service.generate, spec, request)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(PipelineError) as error:
                service.generate(spec, request)
            assert error.value.code == "GENERATION_IN_PROGRESS"
            with repository.connect() as connection:
                row = connection.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity WHERE application_name='pipeline-generator' AND state='idle in transaction'"
                ).fetchone()
                assert row["n"] == 0
        finally:
            release.set()
        assert first.result(timeout=10).spec_id == spec


def test_verification_change_during_generation_prevents_publication(repository):
    _, spec, _ = verified(repository)

    class ChangedVerification:
        def complete(self, system, user, response_schema):
            repository.enqueue_verification(spec)
            return completion()

    with pytest.raises(PipelineError) as error:
        GenerationService(repository, PySparkGenerator(ChangedVerification())).generate(
            spec, uuid4()
        )
    assert error.value.code in {"VERIFICATION_REQUIRED", "CONTEXT_CHANGED"}
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM artifacts WHERE kind='pyspark'"
            ).fetchone()["n"]
            == 0
        )


def test_request_and_artifact_cannot_cross_versions(repository):
    _, first_spec, _ = verified(repository)
    _, other_spec, _ = verified(repository)
    client = FakeLlm(completion(), completion())
    service = GenerationService(repository, PySparkGenerator(client))
    request = uuid4()
    first = service.generate(first_spec, request)
    with pytest.raises(PipelineError) as error:
        service.generate(other_spec, request)
    assert error.value.code == "REQUEST_CONFLICT"
    with pytest.raises(PipelineError) as error:
        repository.get_artifact(other_spec, first.artifact_id)
    assert error.value.code == "NOT_FOUND"
    assert len(client.calls) == 1


def test_failed_artifact_insert_rolls_back_success_manifest(repository):
    _, spec, _ = verified(repository)
    with repository.connect() as connection:
        connection.execute(
            "CREATE FUNCTION reject_script() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.kind='pyspark' THEN RAISE EXCEPTION 'test storage failure'; END IF; RETURN NEW; END $$"
        )
        connection.execute(
            "CREATE TRIGGER reject_script BEFORE INSERT ON artifacts FOR EACH ROW EXECUTE FUNCTION reject_script()"
        )
    request = uuid4()
    with pytest.raises(PipelineError) as error:
        GenerationService(repository, PySparkGenerator(FakeLlm(completion()))).generate(
            spec, request
        )
    assert error.value.code == "STORAGE_ERROR"
    with repository.connect() as connection:
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM artifacts WHERE kind='pyspark'"
            ).fetchone()["n"]
            == 0
        )
        manifest = connection.execute(
            "SELECT content::jsonb AS value FROM artifacts WHERE id=%s", (request,)
        ).fetchone()["value"]
        assert manifest["state"] != "GENERATED"


@pytest.mark.parametrize(
    "bad_output",
    [
        chr(0xD800),
        response(content=chr(0xD800)),
        response(notes=[chr(0xD800)]),
        "[" * 20000 + "0" + "]" * 20000,
    ],
)
def test_malformed_unicode_or_deep_json_fails_request_instead_of_leaving_running(
    repository, bad_output
):
    _, spec, _ = verified(repository)
    client = FakeLlm(completion(bad_output))
    service = GenerationService(repository, PySparkGenerator(client))
    request = uuid4()
    for _ in range(2):
        with pytest.raises(PipelineError) as error:
            service.generate(spec, request)
        assert error.value.code == "LLM_INVALID_RESPONSE"
    assert len(client.calls) == 1
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT content::jsonb AS value FROM artifacts WHERE id=%s", (request,)
        ).fetchone()
        assert row["value"]["state"] == "FAILED"


def test_abandoned_expired_request_requires_explicit_new_key(repository):
    _, spec, _ = verified(repository)
    client = FakeLlm(completion())
    service = GenerationService(repository, PySparkGenerator(client))
    request = uuid4()
    service.issuance.reserve(repository.load_verified_context(spec), request)
    with repository.connect() as connection:
        connection.execute(
            "UPDATE artifacts SET content=jsonb_set(content::jsonb, '{expires_at}', to_jsonb('2000-01-01T00:00:00+00:00'::text))::text WHERE id=%s",
            (request,),
        )
    with pytest.raises(PipelineError) as error:
        service.generate(spec, request)
    assert error.value.code == "GENERATION_EXPIRED"
    assert client.calls == []
    assert service.generate(spec, uuid4()).spec_id == spec
