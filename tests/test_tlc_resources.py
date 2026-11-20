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
