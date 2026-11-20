"""Run one TLC configuration in a private workspace with bounded wall time."""

import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from compiler.formal_check.src.config import MODEL_PATH, SAFETY_CONFIG_PATH, TLC_JAR_PATH
from compiler.formal_check.src.models.models import RunnerBatchOutput, RunnerOutput, Status


def run_tlc(
    timeout: float = 5.0,
    *,
    model_path: Path | str | None = None,
    config_path: Path | str | None = None,
    jar_path: Path | str | None = None,
    workers: int = 1,
) -> RunnerOutput:
    model = Path(model_path) if model_path is not None else MODEL_PATH
    config = Path(config_path) if config_path is not None else SAFETY_CONFIG_PATH
    jar = Path(jar_path or os.getenv("TLC_JAR_PATH", str(TLC_JAR_PATH))).resolve()
    worker_count = max(1, int(workers))
    heap = os.getenv("TLC_HEAP_SIZE", "512m")
    try:
        if not jar.is_file():
            raise FileNotFoundError(f"TLC jar not found: {jar}")
        with tempfile.TemporaryDirectory(prefix="tlc_run_") as tmpdir:
            work_dir = Path(tmpdir)
            shutil.copy(model, work_dir / model.name)
            shutil.copy(config, work_dir / config.name)
            completed = subprocess.run(
                [
                    os.getenv("JAVA_BIN", "java"),
                    f"-Xmx{heap}",
                    "-XX:+UseParallelGC",
                    "-Djava.rmi.server.hostname=127.0.0.1",
                    "-cp",
                    str(jar),
                    "tlc2.TLC",
                    "-workers",
                    str(worker_count),
                    "-config",
                    config.name,
                    "-tool",
                    model.name,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=work_dir,
                timeout=timeout,
                check=False,
            )
        # Invariant failures have nonzero TLC exit codes but are completed checks.
        invariant_failed = re.search(
            r"Invariant [A-Za-z_][A-Za-z_0-9]* is violated", completed.stdout
        )
        temporal_failed = "Temporal properties were violated." in completed.stdout
        if (
            "State limit reached" in completed.stdout + completed.stderr
            or "state space limit" in (completed.stdout + completed.stderr).lower()
        ):
            status = Status.STATE_LIMIT_REACHED
        elif completed.returncode == 0 or (
            completed.returncode in {11, 12, 13} and (invariant_failed or temporal_failed)
        ):
            status = Status.SUCCESS
        elif (
            completed.returncode in {-9, 137}
            or "OutOfMemoryError" in completed.stderr + completed.stdout
        ):
            status = Status.INFRASTRUCTURE_ERROR
        else:
            status = Status.INFRASTRUCTURE_ERROR
        return RunnerOutput(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            status=status,
        )
    except subprocess.TimeoutExpired as exc:
        return RunnerOutput(
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr) or "TLC execution timed out",
            exit_code=-1,
            status=Status.TIMEOUT,
        )
    except (OSError, ValueError) as exc:
        return RunnerOutput(
            stdout="", stderr=str(exc), exit_code=-1, status=Status.INFRASTRUCTURE_ERROR
        )


def _text(value: bytes | str | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def run_tlc_batch(
    model_path: Path | str,
    config_paths: list[Path | str],
    *,
    timeout: float = 30,
    jar_path: Path | str | None = None,
    workers: int | None = None,
    run_tlc_fn=None,
) -> RunnerBatchOutput:
    """Run independent TLC configurations concurrently within one time budget."""
    if not config_paths:
        return RunnerBatchOutput(outputs=[], elapsed_seconds=0)

    started = time.monotonic()
    deadline = started + timeout
    max_workers = max(1, min(len(config_paths), workers or int(os.getenv("TLC_WORKERS", "4"))))
    results: list[RunnerOutput | None] = [None] * len(config_paths)

    def run(index: int, config_path: Path | str) -> tuple[int, RunnerOutput]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return index, RunnerOutput(
                stdout="",
                stderr="TLC execution time budget exhausted",
                exit_code=-1,
                status=Status.TIMEOUT,
            )
        runner = run_tlc_fn or run_tlc
        output = runner(
            timeout=remaining,
            model_path=model_path,
            config_path=config_path,
            jar_path=jar_path,
            # The batch already parallelises independent JVMs. Giving each JVM
            # the full batch size would square the requested thread budget.
            workers=1,
        )
        return index, output

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tlc") as executor:
        futures = [
            executor.submit(run, index, config_path)
            for index, config_path in enumerate(config_paths)
        ]
        for future in as_completed(futures):
            index, output = future.result()
            results[index] = output

    elapsed = time.monotonic() - started
    return RunnerBatchOutput(
        outputs=[output for output in results if output is not None],
        elapsed_seconds=elapsed,
    )
