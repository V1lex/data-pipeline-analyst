import json

import pytest
from agent.analyst.specification_builder import MockSpecificationBuilder
from pipeline_generator.errors import PipelineError

pytestmark = pytest.mark.postgres


def failed_version(repository):
    project = repository.create_project("Revision")
    spec = MockSpecificationBuilder().build_verifiable_specification([])
    spec.formal_model["variables"].append({"name": "data_valid", "type": "boolean"})
    spec.formal_model["initial"]["data_valid"] = False
    spec_id = repository.create_specification(
        project,
        spec,
        "Confirmed requirements",
        [],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    repository.approve_specification(spec_id)
    run = repository.enqueue_verification(spec_id)
    attempt = repository.claim_verification()
    repository.finish_verification(
        run,
        {
            "status": "PROPERTY_VIOLATED",
            "property_id": "P_DeadlockFree",
            "counterexample": {"steps": []},
        },
        attempt["lease_token"],
    )
    return project, spec_id, run, spec


def proposal_for(spec):
    transitions = json.loads(json.dumps(spec.formal_model["transitions"]))
    transitions[0]["effects"].append({"set": ["data_valid", True]})
    return {
        "issue_summary": "Нарушено свойство",
        "proposed_change": "Уточнить условие перехода Write",
        "rationale": "Учитывать состояние записи",
        "patch_data": {"transitions": transitions},
    }


class Model:
    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, system, prompt):
        return json.dumps(self.payload)


def test_accepted_proposal_creates_new_unapproved_version_without_touching_history(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    assert proposal["status"] == "PROPOSED"
    assert proposal["proposal"]["patch_data"]["transitions"] != spec.formal_model["transitions"]
    new_id = service.accept(spec_id, proposal["id"])
    assert service.accept(spec_id, proposal["id"]) == new_id
    newer = repository.get_specification(new_id)
    assert newer["status"] == "DRAFT" and newer["version"] == 2
    assert (
        newer["context_json"]["pipeline_specification"]["sources"]
        == spec.model_dump(mode="json", by_alias=True)["sources"]
    )
    assert repository.get_specification(spec_id)["status"] == "APPROVED"
    assert repository.list_verifications(spec_id)[0]["result_json"]["status"] == "PROPERTY_VIOLATED"
    assert repository.list_verifications(new_id) == []


def test_rejected_or_stale_proposal_never_creates_version(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    service.reject(spec_id, proposal["id"])
    with pytest.raises(PipelineError):
        service.accept(spec_id, proposal["id"])
    assert len(repository.list_specifications(project)) == 1


def test_proposal_is_stale_after_newer_version_or_verification(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    repository.enqueue_verification(spec_id)
    with pytest.raises(PipelineError, match="REVISION_CONFLICT"):
        service.accept(spec_id, proposal["id"])
    assert len(repository.list_specifications(project)) == 1


def test_proposal_cannot_change_properties_or_remove_transitions(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    bad = proposal_for(spec)
    bad["patch_data"]["properties"] = []
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(bad)).propose(spec_id, run)
    bad = proposal_for(spec)
    bad["patch_data"]["transitions"].pop()
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(bad)).propose(spec_id, run)


def test_delete_project_removes_proposal_and_new_version(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    service.accept(spec_id, proposal["id"])
    repository.delete_project(project)
    with repository.connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM revision_proposals").fetchone()["n"] == 0
        )


def test_revision_api_proposes_and_applies_explicitly(repository):
    from agent.analyst.specification_builder import MockSpecificationBuilder
    from api.app import create_app
    from fastapi.testclient import TestClient
    from test_api import CodeModel

    project, spec_id, run, spec = failed_version(repository)
    app = create_app(
        repository=repository,
        analyst_client=Model(proposal_for(spec)),
        builder=MockSpecificationBuilder(),
        generation_client=CodeModel(),
    )
    with TestClient(app) as client:
        response = client.post(f"/api/v1/specifications/{spec_id}/verifications/{run}/proposals")
        assert response.status_code == 201, response.text
        proposal_id = response.json()["id"]
        assert len(repository.list_specifications(project)) == 1
        applied = client.post(f"/api/v1/specifications/{spec_id}/proposals/{proposal_id}/accept")
        assert applied.status_code == 201, applied.text
        assert applied.json()["status"] == "DRAFT"
        assert len(repository.list_specifications(project)) == 2


def test_proposal_cannot_apply_to_another_or_newer_version(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    new = repository.create_specification(
        project,
        spec,
        "new requirements",
        [],
        {"pyspark_version": "3.5.8", "extra_dependencies": []},
    )
    with pytest.raises(PipelineError, match="NOT_FOUND"):
        service.accept(new, proposal["id"])
    with pytest.raises(PipelineError, match="REVISION_CONFLICT"):
        service.accept(spec_id, proposal["id"])
    assert len(repository.list_specifications(project)) == 2


def test_empty_or_noop_model_proposal_is_never_saved(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    payload["patch_data"] = {}
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)
    payload["patch_data"] = {"transitions": spec.formal_model["transitions"]}
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)
    with repository.connect() as connection:
        assert (
            connection.execute("SELECT count(*) AS n FROM revision_proposals").fetchone()["n"] == 0
        )


def test_malformed_transition_names_are_rejected_safely(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    payload["patch_data"]["transitions"][0]["name"] = []
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)


def test_invalid_model_proposal_gets_bounded_correction_attempt(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    bad = proposal_for(spec)
    bad["patch_data"]["transitions"].pop()
    good = proposal_for(spec)

    class CorrectingModel:
        def __init__(self):
            self.calls = []

        def generate_json(self, system, prompt):
            self.calls.append(prompt)
            return json.dumps(bad if len(self.calls) == 1 else good)

    model = CorrectingModel()
    proposal = RevisionService(repository, model).propose(spec_id, run)
    assert proposal["status"] == "PROPOSED"
    assert len(model.calls) == 2
    assert "переход" in model.calls[1].lower()


def test_proposal_becomes_stale_when_dialogue_changes(repository):
    from agent.revision import RevisionService

    project, spec_id, run, spec = failed_version(repository)
    service = RevisionService(repository, Model(proposal_for(spec)))
    proposal = service.propose(spec_id, run)
    lease, snapshot = repository.begin_dialogue(project)
    repository.commit_dialogue(project, lease, snapshot, user="Изменились требования")
    with pytest.raises(PipelineError, match="REVISION_CONFLICT"):
        service.accept(spec_id, proposal["id"])


def test_proposal_cannot_turn_transition_into_guaranteed_noop(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    payload["patch_data"]["transitions"][0]["effects"] = [{"set": ["status", "IDLE"]}]
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)


def test_proposal_cannot_remove_only_producer_of_downstream_guard_state(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    payload["patch_data"]["transitions"][0]["effects"] = [{"set": ["status", "WRITTEN"]}]
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)


def test_proposal_cannot_replace_guard_and_disable_initial_transition(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    payload["patch_data"]["transitions"][0]["guard"] = {"eq": [{"var": "status"}, "WRITTEN"]}
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)


def test_proposal_cannot_narrow_guard_to_make_transition_unreachable(repository):
    from agent.revision import RevisionService

    _, spec_id, run, spec = failed_version(repository)
    payload = proposal_for(spec)
    original = payload["patch_data"]["transitions"][0]["guard"]
    payload["patch_data"]["transitions"][0]["guard"] = {
        "and": [original, {"eq": [{"var": "status"}, "WRITTEN"]}]
    }
    with pytest.raises(PipelineError, match="REVISION_INVALID"):
        RevisionService(repository, Model(payload)).propose(spec_id, run)
