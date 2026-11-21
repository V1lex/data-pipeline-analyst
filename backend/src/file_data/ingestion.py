"""Project-local file ingestion with an idempotent local status outbox."""

import tempfile
from pathlib import Path

from pipeline_generator.errors import PipelineError
from pipeline_generator.storage import _found
from psycopg.types.json import Jsonb

from file_data.profile import ProfileError
from file_data.rules import validate_rules
from file_data.validation import validate_in_subprocess


def process_file_event(
    repository, project_id, technical_id, source_id, required_columns, request_id, *, rules=None
):
    try:
        rules = validate_rules(rules or [])
    except ValueError as exc:
        raise PipelineError("FILE_INVALID", str(exc)) from None
    technical_id = technical_id.strip() if isinstance(technical_id, str) else ""
    if not technical_id or len(technical_id) > 200 or any(ord(c) < 32 for c in technical_id):
        raise PipelineError("FILE_INVALID", "Укажите корректный технический ID файла.")
    if (
        not isinstance(required_columns, list)
        or len(required_columns) > 20
        or any(
            not isinstance(name, str) or not name.strip() or len(name) > 256
            for name in required_columns
        )
        or len(set(required_columns)) != len(required_columns)
    ):
        raise PipelineError("FILE_INVALID", "Укажите корректные уникальные имена колонок.")
    with repository.connect() as connection:
        _found(
            connection.execute(
                "SELECT id FROM projects WHERE id=%s FOR UPDATE", (project_id,)
            ).fetchone()
        )
        event = connection.execute(
            "SELECT * FROM file_ingestion_events WHERE id=%s", (request_id,)
        ).fetchone()
        if event:
            if (
                event["project_id"] != project_id
                or event["technical_id"] != technical_id
                or event["source_id"] != source_id
                or event["required_columns"] != required_columns
                or event["rules"] != rules
            ):
                raise PipelineError(
                    "REQUEST_CONFLICT",
                    "Идентификатор запроса уже использован для другого сообщения.",
                )
            return event | {"delivery": "LOCAL_ONLY"}
        source = None
        if source_id:
            source = _found(
                connection.execute(
                    "SELECT * FROM source_files WHERE id=%s AND project_id=%s",
                    (source_id, project_id),
                ).fetchone()
            )
        previous = connection.execute(
            "SELECT i.source_id,i.validation,f.checksum FROM file_ingestions i JOIN source_files f ON f.id=i.source_id WHERE i.project_id=%s AND i.technical_id=%s",
            (project_id, technical_id),
        ).fetchone()
        if previous:
            if source and source["checksum"] != previous["checksum"]:
                raise PipelineError(
                    "REQUEST_CONFLICT", "Этот технический ID уже связан с другим содержимым файла."
                )
            branch, status, validation = "ALREADY_LOADED", "SUCCESS", previous["validation"]
        else:
            if not source or not (required_columns or rules):
                raise PipelineError(
                    "FILE_INVALID",
                    "Для нового файла выберите источник и хотя бы одно правило проверки.",
                )
            try:
                with tempfile.TemporaryDirectory(prefix="file_ingestion_") as directory:
                    path = Path(directory) / "source"
                    path.write_bytes(source["content"])
                    validation = validate_in_subprocess(
                        path,
                        source["filename"],
                        source["profile"].get("read_options", {}),
                        required_columns,
                        rules,
                    )
            except ProfileError:
                raise PipelineError(
                    "STORAGE_ERROR",
                    "Не удалось выполнить проверку файла; повторите запрос.",
                    retryable=True,
                ) from None
            branch = "NEW_VALID" if validation["valid"] else "NEW_INVALID"
            status = "SUCCESS" if validation["valid"] else "ERROR"
            connection.execute(
                "INSERT INTO file_ingestions(project_id,technical_id,source_id,validation) VALUES (%s,%s,%s,%s)",
                (project_id, technical_id, source_id, Jsonb(validation)),
            )
        saved = connection.execute(
            """INSERT INTO file_ingestion_events(id,project_id,technical_id,source_id,required_columns,rules,branch,status,validation)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                request_id,
                project_id,
                technical_id,
                source_id,
                Jsonb(required_columns),
                Jsonb(rules),
                branch,
                status,
                Jsonb(validation),
            ),
        ).fetchone()
        return saved | {"delivery": "LOCAL_ONLY"}


def list_file_events(repository, project_id):
    with repository.connect() as connection:
        _found(connection.execute("SELECT id FROM projects WHERE id=%s", (project_id,)).fetchone())
        rows = connection.execute(
            "SELECT * FROM file_ingestion_events WHERE project_id=%s ORDER BY created_at DESC,id DESC LIMIT 100",
            (project_id,),
        ).fetchall()
    return [row | {"delivery": "LOCAL_ONLY"} for row in rows]
