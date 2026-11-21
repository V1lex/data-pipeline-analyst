"""Trusted backend/worker persistence. Each method closes its own short transaction."""

import hashlib
import re
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from .errors import PipelineError
from .models import CHECK_STATUSES, Artifact, VerifiedContext, ensure_verified
from .team_contracts import json_object, specification_snapshot


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
        """Apply the canonical Alembic chain; never create a competing schema."""
        from db.migrations import upgrade

        upgrade(self.dsn, schema=self.schema)

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

    def rename_project(self, project_id: UUID, name: str) -> dict:
        name = name.strip()
        if not name or len(name) > 200:
            raise PipelineError(
                "CONTEXT_REQUIRED", "Название должно содержать от 1 до 200 символов."
            )
        with self.connect() as connection:
            return _found(
                connection.execute(
                    "UPDATE projects SET name=%s WHERE id=%s RETURNING id,name",
                    (name, project_id),
                ).fetchone()
            )

    def delete_project(self, project_id: UUID) -> None:
        # Foreign keys cascade to messages, versions, runs, properties and files.
        with self.connect() as connection:
            _found(
                connection.execute(
                    "DELETE FROM projects WHERE id=%s RETURNING id", (project_id,)
                ).fetchone()
            )

    def store_source_file(
        self,
        project_id: UUID,
        filename: str,
        content: bytes,
        profile: dict,
        *,
        max_files: int,
        file_id: UUID | None = None,
    ) -> UUID:
        file_id = file_id or uuid4()
        checksum = hashlib.sha256(content).hexdigest()
        profile = {**profile, "file_id": str(file_id), "checksum": checksum}
        fmt = filename.rsplit(".", 1)[-1].lower()
        with self.connect() as connection:
            _found(
                connection.execute(
                    "SELECT id FROM projects WHERE id=%s FOR UPDATE", (project_id,)
                ).fetchone()
            )
            existing = connection.execute(
                "SELECT project_id,filename,checksum FROM source_files WHERE id=%s", (file_id,)
            ).fetchone()
            if not existing:
                existing = connection.execute(
                    "SELECT f.project_id,f.filename,f.checksum,f.id AS source_id FROM source_upload_aliases a JOIN source_files f ON f.id=a.source_id WHERE a.request_id=%s",
                    (file_id,),
                ).fetchone()
            if existing:
                if (
                    existing["project_id"] != project_id
                    or existing["filename"] != filename
                    or existing["checksum"] != checksum
                ):
                    raise PipelineError(
                        "FILE_INVALID", "Идентификатор загрузки уже использован для другого файла."
                    )
                return existing.get("source_id", file_id)
            duplicate = connection.execute(
                "SELECT id FROM source_files WHERE project_id=%s AND filename=%s AND checksum=%s AND profile->'read_options' IS NOT DISTINCT FROM %s::jsonb ORDER BY created_at,id LIMIT 1",
                (
                    project_id,
                    filename,
                    checksum,
                    Jsonb(profile.get("read_options"))
                    if profile.get("read_options") is not None
                    else None,
                ),
            ).fetchone()
            if duplicate:
                connection.execute(
                    "INSERT INTO source_upload_aliases(request_id,source_id) VALUES (%s,%s)",
                    (file_id, duplicate["id"]),
                )
                return duplicate["id"]
            count = connection.execute(
                "SELECT count(*) AS count FROM source_files WHERE project_id=%s", (project_id,)
            ).fetchone()["count"]
            if count >= max_files:
                raise PipelineError("FILE_LIMIT", "Достигнут лимит числа файлов проекта.")
            connection.execute(
                """INSERT INTO source_files(id,project_id,filename,format,size_bytes,checksum,content,profile)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    file_id,
                    project_id,
                    filename,
                    fmt,
                    len(content),
                    checksum,
                    content,
                    Jsonb(profile),
                ),
            )
        return file_id

    def list_source_files(self, project_id: UUID) -> list[dict]:
        with self.connect() as connection:
            _found(
                connection.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone()
            )
            return connection.execute(
                "SELECT id,project_id,filename,format,size_bytes,checksum,profile,status,created_at FROM source_files WHERE project_id=%s ORDER BY created_at,id",
                (project_id,),
            ).fetchall()

    def get_source_file(self, project_id: UUID, file_id: UUID) -> dict:
        with self.connect() as connection:
            return _found(
                connection.execute(
                    "SELECT * FROM source_files WHERE project_id=%s AND id=%s",
                    (project_id, file_id),
                ).fetchone()
            )

    def selected_source_files(self, project_id: UUID) -> list[dict]:
        with self.connect() as connection:
            project = _found(
                connection.execute(
                    "SELECT selected_source_ids FROM projects WHERE id=%s", (project_id,)
                ).fetchone()
            )
            ids = project["selected_source_ids"]
            if not ids:
                return []
            return connection.execute(
                """SELECT f.id,f.project_id,f.filename,f.format,f.size_bytes,f.checksum,f.profile,f.status,f.created_at
                FROM unnest(%s::uuid[]) WITH ORDINALITY AS selected(id,position)
                JOIN source_files f ON f.id=selected.id AND f.project_id=%s ORDER BY selected.position""",
                (ids, project_id),
            ).fetchall()

    def select_source_files(self, project_id: UUID, file_ids: list[UUID]) -> None:
        if len(file_ids) != len(set(file_ids)):
            raise PipelineError("FILE_INVALID", "Один файл выбран несколько раз.")
        with self.connect() as connection:
            project = _found(
                connection.execute(
                    "SELECT selected_source_ids,analyst_state,lease_token,lease_until FROM projects WHERE id=%s FOR UPDATE",
                    (project_id,),
                ).fetchone()
            )
            busy = connection.execute(
                "SELECT lease_token IS NOT NULL AND lease_until > clock_timestamp() AS busy FROM projects WHERE id=%s",
                (project_id,),
            ).fetchone()["busy"]
            if busy:
                raise PipelineError(
                    "DIALOGUE_BUSY", "Дождитесь завершения запроса аналитика.", retryable=True
                )
            rows = connection.execute(
                "SELECT id FROM source_files WHERE project_id=%s AND id=ANY(%s::uuid[])",
                (project_id, [str(i) for i in file_ids]),
            ).fetchall()
            if len(rows) != len(file_ids):
                raise PipelineError("NOT_FOUND", "Источник не найден в этом проекте.")
            ids = [str(i) for i in file_ids]
            if ids == project["selected_source_ids"]:
                return
            snapshot = dict(project["analyst_state"] or {})
            snapshot.update(
                {
                    "last_response": None,
                    "current_draft": {},
                    "parameters_metadata": [],
                    "state": "insufficient_data",
                }
            )
            connection.execute(
                "UPDATE projects SET selected_source_ids=%s,analyst_state=%s,state_revision=state_revision+1 WHERE id=%s",
                (Jsonb(ids), Jsonb(snapshot), project_id),
            )

    def add_message(self, project_id: UUID, role: str, content: str) -> UUID:
        role = "assistant" if role == "model" else role
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
        model: dict[str, Any] | BaseModel,
        approved_requirements: str,
        message_ids: list[UUID],
        target_environment: dict[str, Any],
        *,
        dialogue_token: UUID | None = None,
    ) -> UUID:
        if not approved_requirements.strip() or len(set(message_ids)) != len(message_ids):
            raise PipelineError("CONTEXT_REQUIRED", "Нужны требования и уникальные UUID сообщений.")
        model, pipeline_specification = specification_snapshot(model)
        spec = uuid4()
        with self.connect() as connection:
            # Serializes version allocation only, not an external operation.
            project_row = _found(
                connection.execute(
                    "SELECT id,selected_source_ids FROM projects WHERE id=%s FOR UPDATE",
                    (project_id,),
                ).fetchone()
            )
            if dialogue_token is not None:
                leased = connection.execute(
                    "SELECT id FROM projects WHERE id=%s AND lease_token=%s AND lease_until>clock_timestamp()",
                    (project_id, dialogue_token),
                ).fetchone()
                if leased is None:
                    raise PipelineError(
                        "DIALOGUE_CONFLICT",
                        "Диалог изменился во время создания версии. Повторите запрос для актуальных требований.",
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
            if pipeline_specification is not None:
                snapshot["pipeline_specification"] = pipeline_specification
            if project_row["selected_source_ids"]:
                files = connection.execute(
                    """SELECT f.id,f.filename,f.format,f.size_bytes,f.checksum,f.profile
                    FROM unnest(%s::uuid[]) WITH ORDINALITY AS selected(id,position)
                    JOIN source_files f ON f.id=selected.id AND f.project_id=%s ORDER BY selected.position""",
                    (project_row["selected_source_ids"], project_id),
                ).fetchall()
                if len(files) != len(project_row["selected_source_ids"]):
                    raise PipelineError("CONTEXT_REQUIRED", "Выбранный источник больше недоступен.")
                snapshot["source_files"] = [{**row, "id": str(row["id"])} for row in files]
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

    def claim_verification(self, lease_seconds: int = 90) -> dict | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        token = uuid4()
        with self.connect() as connection:
            return connection.execute(
                """WITH next AS (
                SELECT id FROM verification_runs
                WHERE status='QUEUED' OR (status='RUNNING' AND lease_until <= clock_timestamp())
                ORDER BY queued_at,id FOR UPDATE SKIP LOCKED LIMIT 1
            ) UPDATE verification_runs r SET status='RUNNING', lease_token=%s,
                lease_until=clock_timestamp()+(%s * interval '1 second'), heartbeat_at=clock_timestamp()
              FROM next WHERE r.id=next.id RETURNING r.*""",
                (token, lease_seconds),
            ).fetchone()

    def heartbeat_verification(self, run_id: UUID, token: UUID, lease_seconds: int = 90) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                """UPDATE verification_runs SET heartbeat_at=clock_timestamp(),
                lease_until=clock_timestamp()+(%s * interval '1 second')
                WHERE id=%s AND status='RUNNING' AND lease_token=%s
                  AND lease_until>clock_timestamp() RETURNING id""",
                (lease_seconds, run_id, token),
            ).fetchone()
            return row is not None

    def finish_verification(
        self, run_id: UUID, result: dict[str, Any] | BaseModel | str, token: UUID
    ) -> None:
        """Worker-only API: caller must supply the actual normalized checker response."""
        result = json_object(result, code="INVALID_VERIFICATION_RESULT")
        if result.get("status") not in CHECK_STATUSES:
            raise PipelineError("INVALID_VERIFICATION_RESULT", "Неизвестный общий статус проверки.")
        with self.connect() as connection:
            run = _found(
                connection.execute(
                    "SELECT * FROM verification_runs WHERE id=%s FOR UPDATE", (run_id,)
                ).fetchone()
            )
            if run["status"] != "RUNNING" or run["lease_token"] != token:
                raise PipelineError("LEASE_CONFLICT", "Попытка проверки устарела.")
            valid = connection.execute(
                "SELECT lease_until > clock_timestamp() AS valid FROM verification_runs WHERE id=%s",
                (run_id,),
            ).fetchone()
            if not valid["valid"]:
                raise PipelineError("LEASE_CONFLICT", "Срок попытки проверки истёк.")
            if result["status"] == "PROPERTY_VIOLATED":
                spec = connection.execute(
                    "SELECT spec_json FROM specifications WHERE id=%s", (run["spec_id"],)
                ).fetchone()
                property_id = result.get("property_id")
                if (
                    property_id
                    not in {item.get("id") for item in spec["spec_json"].get("properties", [])}
                    and property_id != "IsDAG"
                ):
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
                "UPDATE verification_runs SET status='FINISHED',finished_at=clock_timestamp(),result_json=%s,lease_token=NULL,lease_until=NULL,heartbeat_at=NULL WHERE id=%s",
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

    def list_projects(self) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,name,description,created_at,state_revision FROM projects ORDER BY created_at DESC,id"
            ).fetchall()

    def begin_dialogue(self, project_id: UUID) -> tuple[UUID, dict]:
        """Lease a dialogue without holding a connection during external calls."""
        token = uuid4()
        with self.connect() as connection:
            _found(
                connection.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone()
            )
            row = connection.execute(
                "UPDATE projects SET lease_token=%s,lease_until=clock_timestamp()+interval '5 minutes' "
                "WHERE id=%s AND (lease_token IS NULL OR lease_until<clock_timestamp()) RETURNING analyst_state",
                (token, project_id),
            ).fetchone()
            if row is None:
                raise PipelineError(
                    "DIALOGUE_BUSY", "Предыдущий запрос проекта ещё выполняется.", retryable=True
                )
            return token, row["analyst_state"]

    def commit_dialogue(
        self,
        project_id: UUID,
        token: UUID,
        snapshot: dict,
        *,
        user: str | None = None,
        assistant: str | None = None,
        attachments: list[dict] | None = None,
    ) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "UPDATE projects SET analyst_state=%s,state_revision=state_revision+1,lease_token=NULL,lease_until=NULL "
                "WHERE id=%s AND lease_token=%s AND lease_until>clock_timestamp() RETURNING id",
                (Jsonb(snapshot), project_id, token),
            ).fetchone()
            if row is None:
                raise PipelineError("DIALOGUE_CONFLICT", "Диалог изменился; обновите проект.")
            for role, content in [("user", user), ("assistant", assistant)]:
                if content is not None:
                    connection.execute(
                        "INSERT INTO messages(id,project_id,role,content,attachments) VALUES (%s,%s,%s,%s,%s)",
                        (
                            uuid4(),
                            project_id,
                            role,
                            content,
                            Jsonb(attachments or [] if role == "user" else []),
                        ),
                    )

    def abort_dialogue(self, project_id: UUID, token: UUID) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE projects SET lease_token=NULL,lease_until=NULL WHERE id=%s AND lease_token=%s",
                (project_id, token),
            )

    def list_artifacts(self, spec_id: UUID) -> list[dict]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT id,spec_id,kind,filename,created_at FROM artifacts WHERE spec_id=%s AND kind='pyspark' ORDER BY created_at,id",
                (spec_id,),
            ).fetchall()
