"""Durable idempotency records and atomic artifact publication in the six-table schema."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from .errors import PipelineError
from .models import GeneratedArtifact, GeneratedCode, VerifiedContext
from .storage import PostgresRepository, _found


class IssuanceStore:
    def __init__(self, repository: PostgresRepository, expiration_seconds: int = 600):
        if not 1 <= expiration_seconds <= 86400:
            raise ValueError("Generation expiration must be 1–86400 seconds")
        self.repository = repository
        self.expiration_seconds = expiration_seconds

    def reserve(self, context: VerifiedContext, request_id: UUID) -> GeneratedArtifact | None:
        with self.repository.connect() as connection:
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            manifest = {
                "state": "RUNNING",
                "fingerprint": context.fingerprint(),
                "expires_at": (now + timedelta(seconds=self.expiration_seconds)).isoformat(),
            }
            created = connection.execute(
                "INSERT INTO artifacts(id,spec_id,kind,filename,content) VALUES (%s,%s,'generation_manifest',%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
                (
                    request_id,
                    context.spec_id,
                    f"generation-{request_id}.json",
                    json.dumps(manifest),
                ),
            ).fetchone()
            if created is not None:
                return None
            existing = _found(
                connection.execute("SELECT * FROM artifacts WHERE id=%s", (request_id,)).fetchone()
            )
            if existing["spec_id"] != context.spec_id or existing["kind"] != "generation_manifest":
                raise PipelineError(
                    "REQUEST_CONFLICT", "Ключ запроса уже относится к другой версии или записи."
                )
            saved = json.loads(existing["content"])
            if saved["fingerprint"] != context.fingerprint():
                raise PipelineError(
                    "REQUEST_CONFLICT",
                    "Контекст или проверка изменились: нужен новый ключ запроса.",
                )
            if saved["state"] == "GENERATED":
                return GeneratedArtifact.model_validate(saved["result"])
            if saved["state"] == "FAILED":
                error = saved["error"]
                raise PipelineError(
                    error["code"],
                    error["message"],
                    retryable=error["retryable"],
                    details=error["details"],
                )
            if now > datetime.fromisoformat(saved["expires_at"]):
                raise PipelineError(
                    "GENERATION_EXPIRED",
                    "Предыдущий запрос не завершён. Для явного повтора нужен новый ключ.",
                )
            raise PipelineError(
                "GENERATION_IN_PROGRESS", "Генерация этого запроса уже выполняется.", retryable=True
            )

    def finish(
        self, context: VerifiedContext, request_id: UUID, code: GeneratedCode
    ) -> GeneratedArtifact:
        artifact_id = uuid4()
        filename = f"{code.filename.removesuffix('.py')}-{artifact_id.hex}.py"
        result = GeneratedArtifact(
            **{**code.model_dump(), "filename": filename},
            artifact_id=artifact_id,
            spec_id=context.spec_id,
            spec_version=context.spec_version,
            verification_run_id=context.verification_run_id,
            request_id=request_id,
            context_sha256=context.fingerprint(),
        )
        with self.repository.connect() as connection:
            # Enqueue also locks this spec row, so the final check and publication are ordered.
            latest = self.repository._verified_context(connection, context.spec_id, exclusive=True)
            if latest.fingerprint() != context.fingerprint():
                raise PipelineError(
                    "CONTEXT_CHANGED", "Результат проверки изменился во время генерации."
                )
            row = _found(
                connection.execute(
                    "SELECT * FROM artifacts WHERE id=%s FOR UPDATE", (request_id,)
                ).fetchone()
            )
            manifest = json.loads(row["content"])
            if manifest["state"] != "RUNNING" or manifest["fingerprint"] != context.fingerprint():
                raise PipelineError(
                    "REQUEST_CONFLICT", "Попытка генерации уже завершена или изменилась."
                )
            connection.execute(
                "INSERT INTO artifacts(id,spec_id,kind,filename,content) VALUES (%s,%s,'pyspark',%s,%s)",
                (artifact_id, context.spec_id, filename, code.content),
            )
            manifest.update(state="GENERATED", result=result.model_dump(mode="json"))
            connection.execute(
                "UPDATE artifacts SET content=%s WHERE id=%s",
                (json.dumps(manifest, ensure_ascii=False), request_id),
            )
        return result

    def fail(self, request_id: UUID, error: PipelineError) -> None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT content FROM artifacts WHERE id=%s AND kind='generation_manifest' FOR UPDATE",
                (request_id,),
            ).fetchone()
            if row is None:
                return
            manifest = json.loads(row["content"])
            if manifest["state"] == "RUNNING":
                manifest.update(state="FAILED", error=error.to_dict())
                connection.execute(
                    "UPDATE artifacts SET content=%s WHERE id=%s",
                    (json.dumps(manifest, ensure_ascii=False), request_id),
                )
