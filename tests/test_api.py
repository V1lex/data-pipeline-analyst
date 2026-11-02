from uuid import uuid4

import pytest
from agent.analyst.models import AnalystResponse
from fastapi.testclient import TestClient
from pipeline_generator.models import Completion


class Model:
    def __init__(self, specification):
        self.spec = specification

    def generate_response(self, **kwargs):
        return AnalystResponse(
            message="Требования собраны. Проверьте спецификацию.",
            is_complete=True,
            draft=self.spec.model_dump(mode="json", by_alias=True),
        )


class CodeModel:
    def complete(self, *args, **kwargs):
        return Completion(
            text='{"status":"GENERATED","answer":"Готово","filename":"pipeline.py","content":"print(1)\\n","requirements":[],"notes":[],"questions":[]}',
            finish_reason="STOP",
            provider="test",
            model="injected",
        )


@pytest.fixture
def app_client(repository):
    from agent.analyst.specification_builder import MockSpecificationBuilder
    from api.app import create_app

    builder = MockSpecificationBuilder()
    spec = builder.build_verifiable_specification([])
    app = create_app(
        repository=repository,
        analyst_client=Model(spec),
        builder=builder,
        generation_client=CodeModel(),
    )
    with TestClient(app) as client:
        yield client


def test_complete_http_flow_uses_real_postgres_and_worker(app_client, repository):
    from worker.src.runner import process_one

    client = app_client
    project = client.post("/api/v1/projects", json={"name": "MVP"}).json()
    pid = project["id"]
    assert client.get("/api/v1/projects").json()[0]["id"] == pid
    message = client.post(
        f"/api/v1/projects/{pid}/messages", json={"content": "Заказы из parquet в витрину"}
    )
    assert message.status_code == 200, message.text
    assert message.json()["state"] == "draft_complete"
    built = client.post(f"/api/v1/projects/{pid}/specifications/build")
    assert built.status_code == 201, built.text
    sid = built.json()["id"]
    assert client.post(f"/api/v1/specifications/{sid}/verify").status_code == 409
    assert (
        client.post(
            f"/api/v1/specifications/{sid}/generate", json={"request_id": str(uuid4())}
        ).status_code
        == 409
    )
    assert client.post(f"/api/v1/specifications/{sid}/approve").status_code == 200
    verified = client.post(f"/api/v1/specifications/{sid}/verify")
    assert verified.status_code == 202
    assert process_one(repository)
    history = client.get(f"/api/v1/specifications/{sid}").json()
    assert history["verifications"][-1]["result_json"]["status"] == "PROPERTY_HOLDS"
    request = {"request_id": str(uuid4())}
    generated = client.post(f"/api/v1/specifications/{sid}/generate", json=request)
    assert generated.status_code == 200, generated.text
    code = generated.json()
    assert code["content"] == "print(1)\n"
    assert client.post(f"/api/v1/specifications/{sid}/generate", json=request).json() == code
    download = client.get(f"/api/v1/specifications/{sid}/artifacts/{code['artifact_id']}")
    assert download.text == code["content"]
    assert "attachment" in download.headers["content-disposition"]
    assert len(client.get(f"/api/v1/projects/{pid}").json()["messages"]) == 2


def test_manual_model_cannot_supply_a_verification_verdict(app_client):
    response = app_client.post("/api/v1/verifications/result", json={"status": "PROPERTY_HOLDS"})
    assert response.status_code == 404


def test_health_without_gemini_configuration(repository):
    from api.app import create_app

    client = TestClient(create_app(repository=repository))
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_invalid_json_specification_and_missing_ids_are_controlled(app_client):
    assert app_client.get(f"/api/v1/specifications/{uuid4()}").status_code == 404
    assert app_client.post("/api/v1/projects", json={"name": "   "}).status_code == 422
    assert app_client.post("/api/v1/projects/no/messages", json={"content": ""}).status_code == 422


def test_cached_generation_survives_missing_provider_after_restart(
    app_client, repository, monkeypatch
):
    from api.app import create_app

    from worker.src.runner import process_one

    pid = app_client.post("/api/v1/projects", json={"name": "Durable code"}).json()["id"]
    app_client.post(f"/api/v1/projects/{pid}/messages", json={"content": "orders to mart"})
    sid = app_client.post(f"/api/v1/projects/{pid}/specifications/build").json()["id"]
    app_client.post(f"/api/v1/specifications/{sid}/approve")
    app_client.post(f"/api/v1/specifications/{sid}/verify")
    assert process_one(repository)
    body = {"request_id": str(uuid4())}
    saved = app_client.post(f"/api/v1/specifications/{sid}/generate", json=body)
    assert saved.status_code == 200
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with TestClient(create_app(repository=repository)) as restarted:
        replay = restarted.post(f"/api/v1/specifications/{sid}/generate", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json() == saved.json()


def test_stale_build_cannot_attach_to_newer_conversation(app_client, repository, monkeypatch):
    from agent.analyst.specification_builder import MockSpecificationBuilder

    pid = app_client.post("/api/v1/projects", json={"name": "Lease race"}).json()["id"]
    app_client.post(f"/api/v1/projects/{pid}/messages", json={"content": "old requirements"})
    original = MockSpecificationBuilder.build_verifiable_specification

    def expired(self, history):
        result = original(self, history)
        with repository.connect() as con:
            con.execute(
                "UPDATE projects SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",
                (pid,),
            )
        lease, snapshot = repository.begin_dialogue(pid)
        repository.commit_dialogue(
            pid, lease, snapshot, user="new requirements", assistant="new response"
        )
        return result

    monkeypatch.setattr(MockSpecificationBuilder, "build_verifiable_specification", expired)
    response = app_client.post(f"/api/v1/projects/{pid}/specifications/build")
    assert response.status_code == 409, response.text
    assert repository.list_specifications(pid) == []


def test_builder_cannot_silently_change_agreed_source(app_client, monkeypatch):
    from agent.analyst.specification_builder import MockSpecificationBuilder

    pid = app_client.post("/api/v1/projects", json={"name": "Preserve values"}).json()["id"]
    app_client.post(f"/api/v1/projects/{pid}/messages", json={"content": "explicit input"})
    original = MockSpecificationBuilder.build_verifiable_specification

    def changed(self, history):
        result = original(self, history)
        result.sources[0].location = "s3://different/unapproved"
        return result

    monkeypatch.setattr(MockSpecificationBuilder, "build_verifiable_specification", changed)
    response = app_client.post(f"/api/v1/projects/{pid}/specifications/build")
    assert response.status_code == 502, response.text


def test_concurrent_first_requests_share_one_provider(repository, monkeypatch):
    import time
    from concurrent.futures import ThreadPoolExecutor

    import agent.analyst.llm_client as module
    from agent.analyst.specification_builder import MockSpecificationBuilder
    from api.app import create_app

    builder = MockSpecificationBuilder()
    spec = builder.build_verifiable_specification([])
    created, closed = [], []

    class SDK:
        def close(self):
            closed.append(self)

    class Provider(Model):
        def __init__(self, **kwargs):
            created.append(self)
            time.sleep(0.05)
            super().__init__(spec)
            self.client = SDK()

    monkeypatch.setattr(module, "GeminiLLMClient", Provider)
    app = create_app(repository=repository, builder=builder, generation_client=CodeModel())
    with TestClient(app) as client:
        projects = [
            client.post("/api/v1/projects", json={"name": str(i)}).json()["id"] for i in range(2)
        ]
        with ThreadPoolExecutor(2) as pool:
            replies = list(
                pool.map(
                    lambda pid: client.post(
                        f"/api/v1/projects/{pid}/messages", json={"content": "orders"}
                    ),
                    projects,
                )
            )
        assert all(reply.status_code == 200 for reply in replies)
        assert len(created) == 1
    assert len(closed) == 1


def test_rename_project_persists_without_changing_history(app_client):
    client = app_client
    pid = client.post("/api/v1/projects", json={"name": "Old"}).json()["id"]
    client.post(f"/api/v1/projects/{pid}/messages", json={"content": "Orders"})
    client.post(f"/api/v1/projects/{pid}/specifications/build")
    before = client.get(f"/api/v1/projects/{pid}").json()
    response = client.patch(f"/api/v1/projects/{pid}", json={"name": "  Новое название  "})
    assert response.status_code == 200, response.text
    after = client.get(f"/api/v1/projects/{pid}").json()
    assert after["name"] == "Новое название"
    assert after["messages"] == before["messages"]
    assert after["analyst"] == before["analyst"]
    assert after["specifications"] == before["specifications"]
    assert client.get("/api/v1/projects").json()[0]["name"] == "Новое название"


def test_project_management_validates_names_and_missing_ids(app_client):
    pid = app_client.post("/api/v1/projects", json={"name": "Original"}).json()["id"]
    for name in ["   ", "a" * 201]:
        assert app_client.patch(f"/api/v1/projects/{pid}", json={"name": name}).status_code == 422
    missing = uuid4()
    assert (
        app_client.patch(f"/api/v1/projects/{missing}", json={"name": "Valid"}).status_code == 404
    )
    assert app_client.delete(f"/api/v1/projects/{missing}").status_code == 404
    assert app_client.get(f"/api/v1/projects/{pid}").json()["name"] == "Original"


def test_delete_project_cascades_saved_history_and_leaves_other_chats(app_client, repository):
    from worker.src.runner import process_one

    client = app_client
    pid = client.post("/api/v1/projects", json={"name": "Delete this"}).json()["id"]
    other = client.post("/api/v1/projects", json={"name": "Keep this"}).json()["id"]
    client.post(f"/api/v1/projects/{pid}/messages", json={"content": "Orders"})
    sid = client.post(f"/api/v1/projects/{pid}/specifications/build").json()["id"]
    client.post(f"/api/v1/specifications/{sid}/approve")
    client.post(f"/api/v1/specifications/{sid}/verify")
    assert process_one(repository)
    saved = client.post(f"/api/v1/specifications/{sid}/generate", json={"request_id": str(uuid4())})
    assert saved.status_code == 200, saved.text
    run = client.get(f"/api/v1/specifications/{sid}").json()["verifications"][0]
    with repository.connect() as connection:
        connection.execute(
            "INSERT INTO property_results(id,run_id,property_id,status) VALUES (%s,%s,%s,%s)",
            (uuid4(), run["id"], "cascade_test", "PROPERTY_HOLDS"),
        )
    deleted = client.delete(f"/api/v1/projects/{pid}")
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404
    assert client.get(f"/api/v1/specifications/{sid}").status_code == 404
    assert (
        client.get(
            f"/api/v1/specifications/{sid}/artifacts/{saved.json()['artifact_id']}"
        ).status_code
        == 404
    )
    assert [p["id"] for p in client.get("/api/v1/projects").json()] == [other]
    with repository.connect() as connection:
        for table in [
            "messages",
            "specifications",
            "verification_runs",
            "property_results",
            "artifacts",
        ]:
            assert connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0


def test_deleting_project_during_worker_check_discards_result(app_client, repository):
    from worker.src.runner import process_one

    client = app_client
    pid = client.post("/api/v1/projects", json={"name": "In flight"}).json()["id"]
    client.post(f"/api/v1/projects/{pid}/messages", json={"content": "Orders"})
    sid = client.post(f"/api/v1/projects/{pid}/specifications/build").json()["id"]
    client.post(f"/api/v1/specifications/{sid}/approve")
    client.post(f"/api/v1/specifications/{sid}/verify")

    def checker(data):
        assert client.delete(f"/api/v1/projects/{pid}").status_code == 200
        return {"status": "PROPERTY_HOLDS", "engine": "TLC", "statistics": {}}

    assert process_one(repository, checker=checker)
    assert client.get(f"/api/v1/projects/{pid}").status_code == 404
    assert repository.claim_verification() is None
