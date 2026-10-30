"""Trusted backend/worker persistence. Each method closes its own short transaction."""

import re
from importlib.resources import files
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .errors import PipelineError
from .models import CHECK_STATUSES, Artifact, VerifiedContext, ensure_verified


def _found(row):
    if row is None:
        raise PipelineError("NOT_FOUND", "Запись не найдена.")
    return row


class PostgresRepository:
    def __init__(self, dsn: str, *, schema: str = "public"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema) or not dsn:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Нужны PostgreSQL DSN и безопасное имя схемы."
            )
        self.dsn = dsn
        self.schema = schema

    def connect(self):
        return psycopg.connect(
            self.dsn,
            options=f"-csearch_path={self.schema}",
            row_factory=dict_row,
            application_name="pipeline-generator",
            connect_timeout=10,
        )

    def migrate(self) -> None:
        """Explicit, one-time initial migration; fails safely on existing tables."""
        script = (
            files("pipeline_generator").joinpath("migrations/001_initial.sql").read_text("utf-8")
        )
        with self.connect() as connection:
            connection.execute(script)

    def create_project(self, name: str, description: str = "") -> UUID:
        if not name.strip():
            raise PipelineError("CONTEXT_REQUIRED", "Название проекта не должно быть пустым.")
        project = uuid4()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO projects(id,name,description) VALUES (%s,%s,%s)",
                (project, name, description),
            )
        return project

    def get_project(self, project_id: UUID) -> dict:
        with self.connect() as connection:
            return _found(
                connection.execute("SELECT * FROM projects WHERE id=%s", (project_id,)).fetchone()
            )

    def add_message(self, project_id: UUID, role: str, content: str) -> UUID:
        if role not in {"user", "assistant", "system"} or not content.strip():
            raise PipelineError("CONTEXT_REQUIRED", "Укажите роль и непустой текст сообщения.")
        message = uuid4()
        with self.connect() as connection:
            _found(
                connection.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone()
            )
            connection.execute(
                "INSERT INTO messages(id,project_id,role,content) VALUES (%s,%s,%s,%s)",
                (message, project_id, role, content),
            )
        return message

    def list_messages(self, project_id: UUID) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM messages WHERE project_id=%s ORDER BY created_at,id", (project_id,)
            ).fetchall()

    def create_specification(
        self,
        project_id: UUID,
        model: dict[str, Any],
        approved_requirements: str,
        message_ids: list[UUID],
        target_environment: dict[str, Any],
    ) -> UUID:
        if not approved_requirements.strip() or len(set(message_ids)) != len(message_ids):
            raise PipelineError("CONTEXT_REQUIRED", "Нужны требования и уникальные UUID сообщений.")
        spec = uuid4()
        with self.connect() as connection:
            # Serializes version allocation only, not an external operation.
            _found(
                connection.execute(
                    "SELECT id FROM projects WHERE id=%s FOR UPDATE", (project_id,)
                ).fetchone()
            )
            messages = connection.execute(
                "SELECT id,role,content FROM messages WHERE project_id=%s AND id=ANY(%s) ORDER BY created_at,id",
                (project_id, message_ids),
            ).fetchall()
            if len(messages) != len(message_ids):
                raise PipelineError(
                    "CONTEXT_REQUIRED", "Все сообщения снимка должны принадлежать этому проекту."
                )
            snapshot = {
                "approved_requirements": approved_requirements,
                "messages": [{**item, "id": str(item["id"])} for item in messages],
                "target_environment": target_environment,
            }
            version = connection.execute(
                "SELECT COALESCE(MAX(version),0)+1 AS version FROM specifications WHERE project_id=%s",
                (project_id,),
            ).fetchone()["version"]
            connection.execute(
                "INSERT INTO specifications(id,project_id,version,spec_json,context_json,status) VALUES (%s,%s,%s,%s,%s,'DRAFT')",
                (spec, project_id, version, Jsonb(model), Jsonb(snapshot)),
            )
        return spec

    def get_specification(self, spec_id: UUID) -> dict:
        with self.connect() as connection:
            return _found(
                connection.execute(
                    "SELECT * FROM specifications WHERE id=%s", (spec_id,)
                ).fetchone()
            )

    def approve_specification(self, spec_id: UUID) -> None:
        with self.connect() as connection:
            _found(
                connection.execute(
                    "UPDATE specifications SET status='APPROVED' WHERE id=%s RETURNING id",
                    (spec_id,),
                ).fetchone()
            )

    def list_specifications(self, project_id: UUID) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM specifications WHERE project_id=%s ORDER BY version", (project_id,)
            ).fetchall()

    def enqueue_verification(self, spec_id: UUID) -> UUID:
        run = uuid4()
        with self.connect() as connection:
            _found(
                connection.execute(
                    "SELECT id FROM specifications WHERE id=%s FOR UPDATE", (spec_id,)
                ).fetchone()
            )
            connection.execute(
                "INSERT INTO verification_runs(id,spec_id,status) VALUES (%s,%s,'QUEUED')",
                (run, spec_id),
            )
        return run

    def claim_verification(self) -> dict | None:
        with self.connect() as connection:
            return connection.execute("""WITH next AS (
                SELECT id FROM verification_runs WHERE status='QUEUED'
                ORDER BY queued_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE verification_runs r SET status='RUNNING' FROM next
              WHERE r.id=next.id RETURNING r.*""").fetchone()

    def finish_verification(self, run_id: UUID, result: dict[str, Any]) -> None:
        """Worker-only API: caller must supply the actual normalized checker response."""
        if result.get("status") not in CHECK_STATUSES:
            raise PipelineError("INVALID_VERIFICATION_RESULT", "Неизвестный общий статус проверки.")
        with self.connect() as connection:
            run = _found(
                connection.execute(
                    "SELECT * FROM verification_runs WHERE id=%s FOR UPDATE", (run_id,)
                ).fetchone()
            )
            if run["status"] == "FINISHED":
                raise PipelineError(
                    "REQUEST_CONFLICT", "Завершённый результат нельзя перезаписать."
                )
            if result["status"] == "PROPERTY_VIOLATED":
                spec = connection.execute(
                    "SELECT spec_json FROM specifications WHERE id=%s", (run["spec_id"],)
                ).fetchone()
                property_id = result.get("property_id")
                if property_id not in {
                    item.get("id") for item in spec["spec_json"].get("properties", [])
                }:
                    raise PipelineError(
                        "INVALID_VERIFICATION_RESULT",
                        "Нарушенное свойство отсутствует в этой версии.",
                    )
                connection.execute(
                    "INSERT INTO property_results(id,run_id,property_id,status,counterexample,stats,error_message) VALUES (%s,%s,%s,'PROPERTY_VIOLATED',%s,%s,%s)",
                    (
                        uuid4(),
                        run_id,
                        property_id,
                        Jsonb(result.get("counterexample")),
                        Jsonb(result.get("statistics")),
                        result.get("error"),
                    ),
                )
            connection.execute(
                "UPDATE verification_runs SET status='FINISHED',finished_at=clock_timestamp(),result_json=%s WHERE id=%s",
                (Jsonb(result), run_id),
            )

    def list_verifications(self, spec_id: UUID) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM verification_runs WHERE spec_id=%s ORDER BY queued_at,id", (spec_id,)
            ).fetchall()

    def list_property_results(self, run_id: UUID) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM property_results WHERE run_id=%s ORDER BY property_id", (run_id,)
            ).fetchall()

    def _verified_context(
        self, connection, spec_id: UUID, *, exclusive: bool = False
    ) -> VerifiedContext:
        query = (
            "SELECT * FROM specifications WHERE id=%s FOR UPDATE"
            if exclusive
            else "SELECT * FROM specifications WHERE id=%s FOR SHARE"
        )
        spec = _found(connection.execute(query, (spec_id,)).fetchone())
        run = connection.execute(
            "SELECT * FROM verification_runs WHERE spec_id=%s ORDER BY queued_at DESC,id DESC LIMIT 1",
            (spec_id,),
        ).fetchone()
        if spec["status"] != "APPROVED" or run is None or run["status"] != "FINISHED":
            raise PipelineError(
                "VERIFICATION_REQUIRED", "Нужны согласованная версия и завершённая проверка."
            )
        try:
            context = VerifiedContext(
                spec_id=spec_id,
                spec_version=spec["version"],
                verification_run_id=run["id"],
                behavioral_model=spec["spec_json"],
                verification_result=run["result_json"],
                **spec["context_json"],
            )
        except (ValidationError, TypeError) as error:
            raise PipelineError(
                "CONTEXT_REQUIRED", "Сохранённый контекст версии неполон."
            ) from error
        ensure_verified(context)
        return context

    def load_verified_context(self, spec_id: UUID) -> VerifiedContext:
        with self.connect() as connection:
            return self._verified_context(connection, spec_id)

    def get_artifact(self, spec_id: UUID, artifact_id: UUID) -> Artifact:
        with self.connect() as connection:
            row = _found(
                connection.execute(
                    "SELECT id,spec_id,kind,filename,content FROM artifacts WHERE id=%s AND spec_id=%s AND kind='pyspark'",
                    (artifact_id, spec_id),
                ).fetchone()
            )
            return Artifact.model_validate(row)
