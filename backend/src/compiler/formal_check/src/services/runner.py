"""Run one TLC configuration in a private workspace with bounded wall time."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from compiler.formal_check.src.config import MODEL_PATH, SAFETY_CONFIG_PATH, TLC_JAR_PATH
from compiler.formal_check.src.models.models import RunnerOutput, Status


def run_tlc(
    timeout: float = 5.0,
    *,
    model_path: Path | str | None = None,
    config_path: Path | str | None = None,
    jar_path: Path | str | None = None,
) -> RunnerOutput:
    model = Path(model_path) if model_path is not None else MODEL_PATH
    config = Path(config_path) if config_path is not None else SAFETY_CONFIG_PATH
    jar = Path(jar_path or os.getenv("TLC_JAR_PATH", str(TLC_JAR_PATH))).resolve()
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
                    "-Xmx512m",
                    "-XX:+UseParallelGC",
                    "-Djava.rmi.server.hostname=127.0.0.1",
                    "-cp",
                    str(jar),
                    "tlc2.TLC",
                    "-workers",
                    "1",
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
        if completed.returncode == 0 or (completed.returncode in {11, 12} and invariant_failed):
            status = Status.SUCCESS
        elif (
            completed.returncode in {-9, 137}
            or "OutOfMemoryError" in completed.stderr + completed.stdout
        ):
            status = Status.INFRASTRUCTURE_ERROR
        else:
            status = Status.ENGINE_ERROR
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
