from uuid import uuid4

import pytest


class Repository:
    def __init__(self, run=True):
        self.run = {"id": uuid4(), "spec_id": uuid4(), "lease_token": uuid4()} if run else None
        self.saved = []

    def claim_verification(self):
        return self.run

    def get_specification(self, spec_id):
        assert spec_id == self.run["spec_id"]
        return {"spec_json": {"variables": []}}

    def finish_verification(self, run_id, result, token):
        assert token == self.run["lease_token"]
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
    assert repository.saved[0][1]["status"] == "INFRASTRUCTURE_ERROR"


def test_worker_aggregates_validation_stage_to_verdict():
    from worker.src.runner import aggregate_verification

    result = aggregate_verification(
        {
            "validation": {
                "status": "MODEL_INVALID",
                "engine": "TLC",
                "errors": [
                    {"type": "INVALID_INITIAL_VALUE", "location": "initial.x", "message": "bad"}
                ],
            },
            "compilation": None,
            "runner": None,
            "parsed": [],
        }
    )
    assert result["status"] == "MODEL_INVALID"
    assert result["errors"][0]["location"] == "initial.x"


def test_worker_aggregates_safety_reachability_and_temporal_results():
    from worker.src.runner import aggregate_verification

    result = aggregate_verification(
        {
            "validation": {"variables": []},
            "compilation": {},
            "runner": {
                "outputs": [
                    {"status": "success", "exit_code": 0, "stdout": "", "stderr": ""},
                    {"status": "success", "exit_code": 0, "stdout": "", "stderr": ""},
                ]
            },
            "parsed": [
                {
                    "configuration": "safety.cfg",
                    "status": "PROPERTY_HOLDS",
                    "engine": "TLC",
                    "statistics": {"states_generated": 3, "distinct_states": 2, "depth": 2},
                },
                {
                    "configuration": "P.cfg",
                    "status": "PROPERTY_HOLDS",
                    "engine": "TLC",
                    "property_id": "P",
                },
            ],
        }
    )
    assert result["status"] == "PROPERTY_HOLDS"
    assert result["checked_properties"] == ["P"]
    assert result["statistics"]["states_generated"] == 3


def test_worker_reach_typeok_violation_is_model_invalid():
    from worker.src.runner import aggregate_verification

    result = aggregate_verification(
        {
            "validation": {"variables": []},
            "compilation": {},
            "runner": {
                "outputs": [
                    {"status": "success", "exit_code": 0},
                    {"status": "success", "exit_code": 12},
                ]
            },
            "parsed": [
                {
                    "configuration": "safety.cfg",
                    "status": "PROPERTY_HOLDS",
                    "engine": "TLC",
                    "statistics": {"states_generated": 2, "distinct_states": 2, "depth": 2},
                },
                {
                    "configuration": "P.cfg",
                    "status": "PROPERTY_VIOLATED",
                    "engine": "TLC",
                    "property_id": "_FormalCheckTypeOK",
                    "counterexample": {"steps": []},
                },
            ],
        }
    )
    assert result["status"] == "MODEL_INVALID"
    assert result["errors"][0]["type"] == "OUT_OF_DOMAIN_VALUE"


def test_worker_aggregates_unreachable_property():
    from worker.src.runner import aggregate_verification

    result = aggregate_verification(
        {
            "validation": {"variables": []},
            "compilation": {},
            "runner": {
                "outputs": [
                    {"status": "success", "exit_code": 0},
                    {"status": "success", "exit_code": 0},
                ]
            },
            "parsed": [
                {
                    "configuration": "safety.cfg",
                    "status": "PROPERTY_HOLDS",
                    "engine": "TLC",
                    "statistics": {"states_generated": 2, "distinct_states": 2, "depth": 2},
                },
                {
                    "configuration": "P.cfg",
                    "status": "PROPERTY_UNREACHABLE",
                    "engine": "TLC",
                    "property_id": "P",
                    "error": "Target state is unreachable",
                },
            ],
        }
    )
    assert result["status"] == "PROPERTY_UNREACHABLE"
    assert result["property_id"] == "P"


def test_worker_aggregates_isdag_violation():
    from worker.src.runner import aggregate_verification

    result = aggregate_verification(
        {
            "validation": {"variables": []},
            "compilation": {},
            "runner": {"outputs": [{"status": "success", "exit_code": 13}]},
            "parsed": [
                {
                    "configuration": "safety.cfg",
                    "status": "PROPERTY_VIOLATED",
                    "engine": "TLC",
                    "property_id": "IsDAG",
                    "counterexample": {"steps": []},
                }
            ],
        }
    )
    assert result["status"] == "PROPERTY_VIOLATED"
    assert result["property_id"] == "IsDAG"


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


@pytest.mark.parametrize(
    "runner, parsed",
    [
        (
            {"status": "invalid_timeout", "error": "TLC timeout must be a positive finite number"},
            [],
        ),
        ({"outputs": []}, []),
        ({"outputs": [{"status": "success"}]}, []),
    ],
)
def test_worker_does_not_report_success_without_verification_results(runner, parsed):
    from worker.src.runner import aggregate_verification

    result = aggregate_verification({"validation": {}, "runner": runner, "parsed": parsed})
    assert result["status"] == "INFRASTRUCTURE_ERROR"
