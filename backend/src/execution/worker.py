"""Separate queue consumer for constrained Docker executions; never loads provider keys."""

import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event, Thread
from uuid import UUID

from pipeline_generator.errors import PipelineError
from pipeline_generator.storage import PostgresRepository

from execution.storage import ExecutionStore

logger = logging.getLogger(__name__)


def inspect_archive(archive: bytes) -> tuple[dict, bytes | None]:
    if len(archive) > 64 * 1024 * 1024:
        return {"warning": "Результат превышает допустимый размер."}, None
    with tempfile.TemporaryDirectory(prefix="execution_result_") as directory:
        root = Path(directory)
        path = root / "result.zip"
        path.write_bytes(archive)
        env = {
            "PATH": os.defpath,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
        }
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "execution.result_reader"],
                input=json.dumps({"archive": str(path), "directory": str(root)}),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=35,
                env=env,
                check=False,
            )
            preview = json.loads(completed.stdout)
            if not isinstance(preview, dict):
                raise ValueError
            csv_path = root / "result.csv"
            csv_data = (
                csv_path.read_bytes()
                if csv_path.exists() and csv_path.stat().st_size <= 64 * 1024 * 1024
                else None
            )
            return preview, csv_data
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {"warning": "Не удалось построить предпросмотр результата."}, None


def process_one(repository, *, runner=None, inspect_result=inspect_archive) -> bool:
    if runner is None:
        from executor.docker_runner import run_isolated

        runner = run_isolated
    store = ExecutionStore(repository)
    job = store.claim()
    if job is None:
        return False
    stop = Event()

    def heartbeat():
        while not stop.wait(30):
            try:
                if not store.heartbeat(job["id"], job["lease_token"]):
                    return
            except Exception:
                logger.exception("Execution heartbeat failed")

    thread = Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        spec = repository.get_specification(job["spec_id"])
        context = repository.load_verified_context(job["spec_id"])
        if context.verification_run_id != job["verification_run_id"]:
            raise PipelineError("VERIFICATION_REQUIRED", "Проверка версии изменилась до запуска.")
        artifact = repository.get_artifact(job["spec_id"], job["artifact_id"])
        snapshot = {UUID(item["id"]): item for item in context.source_files or []}
        mapping = {key: UUID(value) for key, value in job["inputs"].items()}
        if set(mapping.values()) != set(snapshot):
            raise PipelineError("EXECUTION_INPUT_INVALID", "Источники не совпадают с версией.")
        sources = {}
        for file_id, saved in snapshot.items():
            file = repository.get_source_file(spec["project_id"], file_id)
            if hashlib.sha256(file["content"]).hexdigest() != saved["checksum"]:
                raise PipelineError(
                    "EXECUTION_INPUT_INVALID", "Контрольная сумма источника изменилась."
                )
            sources[file_id] = {"format": file["format"], "content": file["content"]}
        result = runner(
            artifact.content,
            sources,
            mapping,
            timeout=int(os.getenv("EXECUTION_TIMEOUT", "120")),
            parameters=job["parameters"],
            image=os.getenv("EXECUTION_IMAGE", "dpa-spark-runner:local"),
        )
        status = result.get("status")
        if status == "SUCCEEDED":
            archive = result["archive"]
            preview, csv_data = inspect_result(archive)
            store.finish(
                job["id"],
                job["lease_token"],
                status,
                archive=archive,
                preview=preview,
                csv_data=csv_data,
            )
        else:
            error = str(result.get("error") or "Выполнение не завершилось.")
            diagnostic = str(result.get("diagnostic") or "")[-4000:]
            validation = [
                line[:300]
                for line in diagnostic.splitlines()
                if line.startswith("VALIDATION ERROR:")
            ][:5]
            detail = (
                "\n".join(validation)
                if validation
                else "Проверьте входные данные, параметры и доступные зависимости."
            )
            store.finish(
                job["id"],
                job["lease_token"],
                status if status in {"FAILED", "TIMEOUT"} else "FAILED",
                error=(error + "\n" + detail)[:2000],
            )
    except PipelineError as error:
        if error.code != "LEASE_CONFLICT":
            try:
                store.finish(job["id"], job["lease_token"], "FAILED", error=error.message)
            except PipelineError:
                pass
    except Exception:
        logger.exception("Execution job failed")
        try:
            store.finish(
                job["id"], job["lease_token"], "FAILED", error="Ошибка окружения исполнения."
            )
        except PipelineError:
            pass
    finally:
        stop.set()
        thread.join()
    return True


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    repository = PostgresRepository(
        os.environ["DATABASE_URL"], schema=os.getenv("DATABASE_SCHEMA", "public")
    )
    stopped = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            if not process_one(repository):
                stopped.wait(1)
        except Exception:
            logger.exception("Execution queue unavailable")
            stopped.wait(2)


if __name__ == "__main__":
    main()
