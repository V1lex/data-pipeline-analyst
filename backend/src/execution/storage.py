"""Queue and fence isolated computation runs."""

import json
import re
from uuid import UUID, uuid4

from pipeline_generator.errors import PipelineError
from pipeline_generator.storage import _found
from psycopg.types.json import Jsonb


class ExecutionStore:
    def __init__(self, repository):
        self.repository = repository

    def enqueue(
        self,
        spec_id: UUID,
        artifact_id: UUID,
        mapping: dict[str, UUID],
        request_id: UUID,
        parameters: dict[str, str] | None = None,
    ) -> UUID:
        if not mapping or len(mapping) > 20 or len(set(mapping.values())) != len(mapping):
            raise PipelineError("EXECUTION_INPUT_INVALID", "Укажите каждый источник один раз.")
        if any(
            not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", key) or key == "--output" for key in mapping
        ):
            raise PipelineError("EXECUTION_INPUT_INVALID", "Некорректные имена аргументов.")
        parameters = parameters or {}
        if (
            len(parameters) > 20
            or set(parameters) & set(mapping)
            or any(
                not re.fullmatch(r"--[a-z][a-z0-9-]{0,40}", key)
                or key == "--output"
                or not isinstance(value, str)
                or len(value) > 512
                or "\x00" in value
                for key, value in parameters.items()
            )
        ):
            raise PipelineError("EXECUTION_INPUT_INVALID", "Некорректные дополнительные параметры.")
        saved_mapping = {key: str(value) for key, value in mapping.items()}
        with self.repository.connect() as connection:
            existing = connection.execute(
                "SELECT spec_id,artifact_id,inputs,parameters FROM execution_runs WHERE id=%s",
                (request_id,),
            ).fetchone()
            if existing:
                if (
                    existing["spec_id"] != spec_id
                    or existing["artifact_id"] != artifact_id
                    or existing["inputs"] != saved_mapping
                    or existing["parameters"] != parameters
                ):
                    raise PipelineError(
                        "REQUEST_CONFLICT", "Ключ запуска уже использован с другими параметрами."
                    )
                return request_id
            context = self.repository._verified_context(connection, spec_id, exclusive=True)
            if context.target_environment.get(
                "pyspark_version"
            ) != "3.5.8" or context.target_environment.get("extra_dependencies", []):
                raise PipelineError(
                    "CONFIGURATION_ERROR",
                    "Исполнитель поддерживает PySpark 3.5.8 без дополнительных зависимостей.",
                )
            source_ids = {item["id"] for item in context.source_files or []}
            if not source_ids or source_ids != set(saved_mapping.values()):
                raise PipelineError(
                    "EXECUTION_INPUT_INVALID", "Укажите только все источники этой версии."
                )
            artifact = connection.execute(
                "SELECT id FROM artifacts WHERE id=%s AND spec_id=%s AND kind='pyspark'",
                (artifact_id, spec_id),
            ).fetchone()
            if not artifact:
                raise PipelineError("NOT_FOUND", "Сохранённый код этой версии не найден.")
            manifests = connection.execute(
                "SELECT content FROM artifacts WHERE spec_id=%s AND kind='generation_manifest'",
                (spec_id,),
            ).fetchall()
            matched = False
            for item in manifests:
                try:
                    manifest = json.loads(item["content"])
                    result = manifest.get("result", {})
                    if (
                        manifest.get("state") == "GENERATED"
                        and manifest.get("fingerprint") == context.fingerprint()
                        and result.get("artifact_id") == str(artifact_id)
                        and result.get("verification_run_id") == str(context.verification_run_id)
                    ):
                        matched = True
                        break
                except (ValueError, TypeError, AttributeError):
                    continue
            if not matched:
                raise PipelineError(
                    "VERIFICATION_REQUIRED", "Код не связан с актуальной проверкой этой версии."
                )
            inserted = connection.execute(
                """INSERT INTO execution_runs(id,spec_id,artifact_id,verification_run_id,inputs,parameters,status)
                VALUES (%s,%s,%s,%s,%s,%s,'QUEUED') ON CONFLICT(id) DO NOTHING RETURNING id""",
                (
                    request_id,
                    spec_id,
                    artifact_id,
                    context.verification_run_id,
                    Jsonb(saved_mapping),
                    Jsonb(parameters),
                ),
            ).fetchone()
            if not inserted:
                existing = _found(
                    connection.execute(
                        "SELECT spec_id,artifact_id,inputs,parameters FROM execution_runs WHERE id=%s",
                        (request_id,),
                    ).fetchone()
                )
                if (
                    existing["spec_id"] != spec_id
                    or existing["artifact_id"] != artifact_id
                    or existing["inputs"] != saved_mapping
                    or existing["parameters"] != parameters
                ):
                    raise PipelineError(
                        "REQUEST_CONFLICT", "Ключ запуска уже использован с другими параметрами."
                    )
            return request_id

    def claim(self, lease_seconds: int = 180):
        token = uuid4()
        with self.repository.connect() as connection:
            return connection.execute(
                """WITH next AS (
                SELECT id FROM execution_runs WHERE status='QUEUED' OR (status='RUNNING' AND lease_until<=clock_timestamp())
                ORDER BY queued_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE execution_runs r SET status='RUNNING',started_at=COALESCE(started_at,clock_timestamp()),
                lease_token=%s,lease_until=clock_timestamp()+(%s * interval '1 second')
                FROM next WHERE r.id=next.id RETURNING r.*""",
                (token, lease_seconds),
            ).fetchone()

    def heartbeat(self, run_id: UUID, token: UUID, lease_seconds: int = 180) -> bool:
        with self.repository.connect() as connection:
            return (
                connection.execute(
                    """UPDATE execution_runs SET lease_until=clock_timestamp()+(%s * interval '1 second')
                WHERE id=%s AND status='RUNNING' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING id""",
                    (lease_seconds, run_id, token),
                ).fetchone()
                is not None
            )

    def finish(
        self,
        run_id: UUID,
        token: UUID,
        status: str,
        *,
        archive: bytes | None = None,
        preview: dict | None = None,
        csv_data: bytes | None = None,
        error: str | None = None,
    ):
        if status not in {"SUCCEEDED", "FAILED", "TIMEOUT"} or (
            (archive is not None) != (status == "SUCCEEDED")
        ):
            raise ValueError("Invalid execution result")
        with self.repository.connect() as connection:
            row = connection.execute(
                """UPDATE execution_runs SET status=%s,finished_at=clock_timestamp(),
                lease_token=NULL,lease_until=NULL,result_archive=%s,result_csv=%s,result_preview=%s,error=%s
                WHERE id=%s AND status='RUNNING' AND lease_token=%s AND lease_until>clock_timestamp() RETURNING id""",
                (
                    status,
                    archive,
                    csv_data,
                    Jsonb(preview) if preview is not None else None,
                    error,
                    run_id,
                    token,
                ),
            ).fetchone()
            if row is None:
                raise PipelineError("LEASE_CONFLICT", "Попытка запуска устарела или проект удалён.")

    def list(self, spec_id: UUID):
        with self.repository.connect() as connection:
            _found(
                connection.execute(
                    "SELECT id FROM specifications WHERE id=%s", (spec_id,)
                ).fetchone()
            )
            return connection.execute(
                "SELECT id,spec_id,artifact_id,verification_run_id,inputs,parameters,status,queued_at,started_at,finished_at,error,result_preview FROM execution_runs WHERE spec_id=%s ORDER BY queued_at,id",
                (spec_id,),
            ).fetchall()

    def get(self, spec_id: UUID, run_id: UUID, *, include_archive: bool = False):
        with self.repository.connect() as connection:
            row = _found(
                connection.execute(
                    "SELECT * FROM execution_runs WHERE id=%s AND spec_id=%s", (run_id, spec_id)
                ).fetchone()
            )
            if not include_archive:
                row.pop("result_archive")
                row.pop("result_csv")
            row.pop("lease_token")
            return row
