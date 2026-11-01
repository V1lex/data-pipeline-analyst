from uuid import uuid4

import pytest


class Repository:
    def __init__(self, run=True):
        self.run = {"id": uuid4(), "spec_id": uuid4()} if run else None
        self.saved = []

    def claim_verification(self):
        return self.run

    def get_specification(self, spec_id):
        assert spec_id == self.run["spec_id"]
        return {"spec_json": {"variables": []}}

    def finish_verification(self, run_id, result):
        self.saved.append((run_id, result))


def test_worker_passes_exact_checker_result_and_reports_empty_queue():
    from worker.src.runner import process_one

    repository = Repository()
    verdict = {"status": "TIMEOUT", "engine": "TLC", "error": "Execution timeout"}

    def check(data):
        assert data == {"variables": []}
        return verdict

    assert process_one(repository, checker=check) is True
    assert repository.saved == [(repository.run["id"], verdict)]
    assert process_one(Repository(run=False), checker=check) is False


def test_worker_finishes_unexpected_checker_exception_as_error():
    from worker.src.runner import process_one

    repository = Repository()

    def check(data):
        raise RuntimeError("engine crashed")

    assert process_one(repository, checker=check)
    assert repository.saved[0][1]["status"] == "ENGINE_ERROR"


@pytest.mark.postgres
def test_worker_persists_real_tlc_outcomes(repository):
    from pathlib import Path

    from worker.src.runner import process_one

    if not Path("worker/tla2tools.jar").is_file():
        pytest.skip("TLC jar not installed")
    project = repository.create_project("Worker integration")
    data = {
        "variables": [{"name": "ready", "type": "boolean"}],
        "initial": {"ready": False},
        "transitions": [{"name": "Finish", "effects": [{"set": ["ready", True]}]}],
        "properties": [
            {"id": "Ready", "type": "reachable", "expression": {"eq": [{"var": "ready"}, True]}}
        ],
    }
    specification = repository.create_specification(project, data, "Reach ready state", [], {})
    repository.approve_specification(specification)
    run = repository.enqueue_verification(specification)
    assert process_one(repository)
    completed = repository.list_verifications(specification)
    assert completed[0]["id"] == run
    assert completed[0]["status"] == "FINISHED"
    assert completed[0]["result_json"]["status"] == "PROPERTY_HOLDS", completed
    assert completed[0]["result_json"]["checked_properties"] == ["Ready"]
    assert not process_one(repository)
