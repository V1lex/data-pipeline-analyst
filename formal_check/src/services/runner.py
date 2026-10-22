import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.config import CONFIG_PATH, MODEL_PATH, TLC_JAR_PATH
from src.models.models import RunnerOutput, Status

logger = logging.getLogger(__name__)


def run_tlc(timeout: float = 5.0) -> RunnerOutput:
    try:
        with tempfile.TemporaryDirectory(prefix="tlc_") as tmpdir:
            work_dir = Path(tmpdir)
            shutil.copy(MODEL_PATH, work_dir)
            shutil.copy(CONFIG_PATH, work_dir)
            result = subprocess.run(
                [
                    "java",
                    "-Xmx2g",
                    "-XX:+UseParallelGC",
                    "-cp",
                    TLC_JAR_PATH,
                    "tlc2.TLC",
                    MODEL_PATH.name,
                ],
                capture_output=True,
                text=True,
                cwd=work_dir,
                timeout=timeout,
            )

        if result.returncode == 0:
            return RunnerOutput(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                status=Status.SUCCESS,
            )
        elif result.returncode in [1, 125, 126, 127]:
            logger.error(f"Execution error: {result.stderr}")
            return RunnerOutput(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                status=Status.INFRASTRUCTURE_ERROR,
            )
        elif result.returncode == 137:
            logger.error("Infrastructure error: Out of memory.")
            return RunnerOutput(
                stdout=result.stdout,
                stderr="Out of memory error.",
                exit_code=result.returncode,
                status=Status.INFRASTRUCTURE_ERROR,
            )
        elif result.returncode == 124:
            logger.error("Execution timeout.")
            return RunnerOutput(
                stdout=result.stdout,
                stderr="Execution timeout.",
                exit_code=result.returncode,
                status=Status.TIMEOUT,
            )
        else:
            logger.error(f"Unknown error occurred: {result.stderr}")
            return RunnerOutput(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                status=Status.INFRASTRUCTURE_ERROR,
            )

    except subprocess.TimeoutExpired as e:
        logger.error(f"Timeout running TLC: {e}")
        return RunnerOutput(
            stdout="",
            stderr=str(e),
            exit_code=-1,
            status=Status.TIMEOUT,
        )
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return RunnerOutput(
            stdout="",
            stderr=str(e),
            exit_code=-1,
            status=Status.INFRASTRUCTURE_ERROR,
        )
