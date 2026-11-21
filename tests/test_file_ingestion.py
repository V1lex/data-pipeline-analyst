from uuid import uuid4

import pytest
from file_data.profile import profile_file


def test_full_file_validation_finds_missing_values_after_profile_sample(tmp_path):
    from file_data.validation import validate_full_file

    source = tmp_path / "input.csv"
    source.write_text("id,amount\n" + "A,5\n" * 1000 + "B,\n", encoding="utf-8")
    result = validate_full_file(
        source, "input.csv", {"encoding": "utf-8-sig", "delimiter": ","}, ["amount"]
    )
    assert result["valid"] is False
    assert result["checked_rows"] == 1001
    assert result["errors"][0]["column"] == "amount"


@pytest.mark.postgres
def test_file_workflow_valid_invalid_and_duplicate_are_recorded(repository, tmp_path):
    from file_data.ingestion import list_file_events, process_file_event

    project = repository.create_project("DAG demo")
    path = tmp_path / "valid.csv"
    path.write_text("id,amount\nA,10\n", encoding="utf-8")
    valid = repository.store_source_file(
        project, path.name, path.read_bytes(), profile_file(path, path.name, ""), max_files=20
    )
    path = tmp_path / "invalid.csv"
    path.write_text("id,amount\nB,\n", encoding="utf-8")
    invalid = repository.store_source_file(
        project, path.name, path.read_bytes(), profile_file(path, path.name, ""), max_files=20
    )
    first = process_file_event(repository, project, "file-001", valid, ["amount"], uuid4())
    duplicate = process_file_event(repository, project, "file-001", valid, ["amount"], uuid4())
    bad = process_file_event(repository, project, "file-002", invalid, ["amount"], uuid4())
    assert (first["branch"], first["status"]) == ("NEW_VALID", "SUCCESS")
    assert (duplicate["branch"], duplicate["status"]) == ("ALREADY_LOADED", "SUCCESS")
    assert (bad["branch"], bad["status"]) == ("NEW_INVALID", "ERROR")
    assert len(list_file_events(repository, project)) == 3


@pytest.mark.postgres
def test_request_id_retry_and_technical_id_conflict(repository, tmp_path):
    from file_data.ingestion import process_file_event
    from pipeline_generator.errors import PipelineError

    project = repository.create_project("Retry")
    one = tmp_path / "one.csv"
    one.write_text("id\nA\n")
    first = repository.store_source_file(
        project, one.name, one.read_bytes(), profile_file(one, one.name, ""), max_files=20
    )
    two = tmp_path / "two.csv"
    two.write_text("id\nB\n")
    second = repository.store_source_file(
        project, two.name, two.read_bytes(), profile_file(two, two.name, ""), max_files=20
    )
    request_id = uuid4()
    event = process_file_event(repository, project, "ID-1", first, ["id"], request_id)
    assert (
        process_file_event(repository, project, "ID-1", first, ["id"], request_id)["id"]
        == event["id"]
    )
    with pytest.raises(PipelineError, match="REQUEST_CONFLICT"):
        process_file_event(repository, project, "ID-2", first, ["id"], request_id)
    with pytest.raises(PipelineError, match="REQUEST_CONFLICT"):
        process_file_event(repository, project, "ID-1", second, ["id"], uuid4())


@pytest.mark.postgres
def test_concurrent_same_technical_id_loads_only_once(repository, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from file_data.ingestion import list_file_events, process_file_event

    project = repository.create_project("Concurrent")
    path = tmp_path / "input.csv"
    path.write_text("id\nA\n")
    source = repository.store_source_file(
        project, path.name, path.read_bytes(), profile_file(path, path.name, ""), max_files=20
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        events = list(
            pool.map(
                lambda _: process_file_event(repository, project, "ID-1", source, ["id"], uuid4()),
                range(2),
            )
        )
    assert {event["branch"] for event in events} == {"NEW_VALID", "ALREADY_LOADED"}
    assert len(list_file_events(repository, project)) == 2


@pytest.mark.postgres
def test_duplicate_does_not_claim_prior_failed_validation_was_successful(repository, tmp_path):
    from file_data.ingestion import process_file_event

    project = repository.create_project("Invalid duplicate")
    path = tmp_path / "input.csv"
    path.write_text("id,amount\nA,\n")
    source = repository.store_source_file(
        project, path.name, path.read_bytes(), profile_file(path, path.name, ""), max_files=20
    )
    first = process_file_event(repository, project, "ID-1", source, ["amount"], uuid4())
    repeated = process_file_event(repository, project, "ID-1", None, [], uuid4())
    assert first["status"] == "ERROR"
    assert repeated["branch"] == "ALREADY_LOADED" and repeated["status"] == "SUCCESS"
    assert repeated["validation"]["valid"] is False


@pytest.mark.postgres
def test_ingestion_api_exposes_recorded_local_status(repository, tmp_path):
    from api.app import create_app
    from fastapi.testclient import TestClient

    app_client = TestClient(create_app(repository=repository))
    project = repository.create_project("API")
    source_path = tmp_path / "input.csv"
    source_path.write_text("id\nA\n")
    source = repository.store_source_file(
        project,
        source_path.name,
        source_path.read_bytes(),
        profile_file(source_path, source_path.name, ""),
        max_files=20,
    )
    payload = {
        "technical_id": "ext-01",
        "source_id": str(source),
        "required_columns": ["id"],
        "request_id": str(uuid4()),
    }
    response = app_client.post(f"/api/v1/projects/{project}/file-ingestions", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "SUCCESS"
    assert response.json()["delivery"] == "LOCAL_ONLY"
    assert (
        app_client.get(f"/api/v1/projects/{project}/file-ingestions").json()[0]["id"]
        == payload["request_id"]
    )
