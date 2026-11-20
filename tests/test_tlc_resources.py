from pathlib import Path


def test_parallel_configs_do_not_multiply_tlc_thread_budget():
    from compiler.formal_check.src.models.models import RunnerOutput, Status
    from compiler.formal_check.src.services.runner import run_tlc_batch

    seen = []

    def run(**kwargs):
        seen.append(kwargs["workers"])
        return RunnerOutput(stdout="", stderr="", exit_code=0, status=Status.SUCCESS)

    run_tlc_batch(Path("model"), [Path("a"), Path("b")], workers=2, run_tlc_fn=run)
    assert seen == [1, 1]


def test_state_budget_stops_model_before_launch_when_domain_is_too_large(monkeypatch):
    from agent.analyst.specification_builder import MockSpecificationBuilder
    from compiler.formal_check.src.services.checker import check_model

    from worker.src.runner import aggregate_verification

    model = MockSpecificationBuilder().build_verifiable_specification([]).formal_model
    monkeypatch.setenv("TLC_MAX_STATES", "2")
    result = check_model(model)
    assert aggregate_verification(result)["status"] == "STATE_LIMIT_REACHED"
    assert "3" in result["runner"]["outputs"][0]["stderr"]
