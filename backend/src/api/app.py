"""Local MVP API. Verification verdicts can only be written by the worker."""

import asyncio
import hashlib
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import UUID

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pipeline_generator.errors import PipelineError
from pipeline_generator.generator import PySparkGenerator
from pipeline_generator.service import GenerationService
from pipeline_generator.storage import PostgresRepository

from api.schemas import (
    ExecutionRequest,
    GenerationRequest,
    MessageRequest,
    ProjectRenameRequest,
    ProjectRequest,
    SourceSelectionRequest,
)

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


def create_app(*, repository=None, analyst_client=None, builder=None, generation_client=None):
    @lru_cache(maxsize=1)
    def repo():
        if repository is not None:
            return repository
        dsn = os.getenv("DATABASE_URL", "")
        if not dsn:
            raise PipelineError("CONFIGURATION_ERROR", "Настройте DATABASE_URL для PostgreSQL.")
        return PostgresRepository(dsn, schema=os.getenv("DATABASE_SCHEMA", "public"))

    provider_lock = Lock()

    @lru_cache(maxsize=1)
    def cached_providers():
        from agent.analyst.polza import PolzaLLMClient
        from agent.analyst.specification_builder import SpecificationBuilder

        try:
            client = analyst_client or PolzaLLMClient()
            spec_builder = builder or SpecificationBuilder(
                client=client, model_name=client.model_name
            )
            generator = generation_client or client
            return client, spec_builder, generator
        except ValueError:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Настройте POLZA_API_KEY и POLZA_MODEL."
            ) from None

    def providers():
        with provider_lock:
            return cached_providers()

    class LazyGenerationClient:
        def complete(self, *args, **kwargs):
            return providers()[2].complete(*args, **kwargs)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if cached_providers.cache_info().currsize and analyst_client is None:
            providers()[0].close()

    app = FastAPI(title="Data Pipeline Analyst", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(PipelineError)
    async def pipeline_error(request: Request, error: PipelineError):
        status = (
            404 if error.code == "NOT_FOUND" else (502 if error.code.startswith("LLM_") else 409)
        )
        if error.code in {"FILE_INVALID", "FILE_TOO_LARGE", "FILE_LIMIT"}:
            status = 422
        if error.code in {"CONFIGURATION_ERROR", "STORAGE_ERROR"}:
            status = 503
        return JSONResponse(status_code=status, content={"error": error.to_dict()})

    @app.exception_handler(psycopg.Error)
    async def database_error(request, error):
        logger.warning("PostgreSQL operation failed: %s", type(error).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "STORAGE_ERROR",
                    "message": "PostgreSQL недоступен или миграции не применены.",
                }
            },
        )

    def model_call(action):
        try:
            return action()
        except PipelineError:
            raise
        except Exception as error:
            logger.warning("Model operation failed: %s", type(error).__name__)
            raise PipelineError(
                "LLM_REQUEST_FAILED",
                "Модель не смогла обработать запрос. Проверьте настройки и попробуйте ещё раз.",
                retryable=True,
            ) from None

    def workflow():
        from agent.workflow import AnalystWorkflow

        client, spec_builder, _ = providers()
        return AnalystWorkflow(repo(), client, spec_builder)

    @app.get("/api/v1/health")
    def health():
        with repo().connect() as connection:
            connection.execute("SELECT id FROM projects LIMIT 0")
        return {"status": "ok"}

    @app.get("/api/v1/projects")
    def projects():
        return repo().list_projects()

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: ProjectRequest):
        project_id = repo().create_project(body.name, body.description)
        return {"id": project_id, "name": body.name, "description": body.description}

    @app.get("/api/v1/projects/{project_id}")
    def project(project_id: UUID):
        record = repo().get_project(project_id)
        return {
            key: record[key]
            for key in ("id", "name", "description", "created_at", "state_revision")
        } | {
            "analyst": record["analyst_state"],
            "messages": repo().list_messages(project_id),
            "specifications": repo().list_specifications(project_id),
        }

    @app.patch("/api/v1/projects/{project_id}")
    def rename_project(project_id: UUID, body: ProjectRenameRequest):
        return repo().rename_project(project_id, body.name)

    @app.delete("/api/v1/projects/{project_id}")
    def delete_project(project_id: UUID):
        repo().delete_project(project_id)
        return {"id": project_id}

    @app.get("/api/v1/projects/{project_id}/sources")
    def list_sources(project_id: UUID):
        return repo().list_source_files(project_id)

    @app.get("/api/v1/projects/{project_id}/sources/selection")
    def selected_sources(project_id: UUID):
        return repo().selected_source_files(project_id)

    @app.put("/api/v1/projects/{project_id}/sources/selection")
    def select_sources(project_id: UUID, body: SourceSelectionRequest):
        repo().select_source_files(project_id, body.file_ids)
        return repo().selected_source_files(project_id)

    @app.get("/api/v1/projects/{project_id}/sources/{file_id}")
    def source_profile(project_id: UUID, file_id: UUID):
        source = repo().get_source_file(project_id, file_id)
        return {key: value for key, value in source.items() if key != "content"}

    @app.post("/api/v1/projects/{project_id}/sources", status_code=201)
    async def upload_source(project_id: UUID, request: Request):
        from file_data.profile import ProfileError, profile_in_subprocess

        repo().get_project(project_id)
        max_size = max(1, int(os.getenv("SOURCE_MAX_BYTES", "10485760")))
        max_files = max(1, int(os.getenv("SOURCE_MAX_FILES", "20")))
        try:
            length = int(request.headers.get("content-length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > max_size + 131072:
            raise PipelineError("FILE_TOO_LARGE", "Укажите допустимый размер файла при загрузке.")
        received = 0
        receive = request.receive

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > max_size + 131072:
                raise PipelineError("FILE_TOO_LARGE", "Размер файла превышает допустимый предел.")
            return message

        bounded_request = Request(request.scope, receive=bounded_receive)
        async with bounded_request.form(max_files=1, max_fields=4, max_part_size=131072) as form:
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise PipelineError("FILE_INVALID", "Выберите файл для загрузки.")
            filename = str(upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
            if not filename or len(filename) > 255 or any(ord(char) < 32 for char in filename):
                raise PipelineError("FILE_INVALID", "Некорректное имя файла.")
            content = await upload.read(max_size + 1)
            if len(content) > max_size:
                raise PipelineError("FILE_TOO_LARGE", "Размер файла превышает допустимый предел.")
            options = {key: form[key] for key in ("delimiter", "encoding") if key in form}
            try:
                request_id = UUID(str(form["request_id"])) if "request_id" in form else None
            except (ValueError, TypeError):
                raise PipelineError(
                    "FILE_INVALID", "Некорректный идентификатор загрузки."
                ) from None
        try:
            with tempfile.TemporaryDirectory(prefix="source_profile_") as directory:
                path = Path(directory) / "source"
                path.write_bytes(content)
                profile = await asyncio.to_thread(
                    profile_in_subprocess,
                    path,
                    filename,
                    hashlib.sha256(content).hexdigest(),
                    options,
                )
        except ProfileError as error:
            raise PipelineError("FILE_INVALID", str(error)) from None
        file_id = repo().store_source_file(
            project_id, filename, content, profile, max_files=max_files, file_id=request_id
        )
        return source_profile(project_id, file_id)

    @app.post("/api/v1/projects/{project_id}/messages")
    def send(project_id: UUID, body: MessageRequest):
        return model_call(lambda: workflow().send(project_id, body.content))

    @app.post("/api/v1/projects/{project_id}/specifications/build", status_code=201)
    def build(project_id: UUID):
        return model_call(lambda: workflow().build(project_id))

    @app.get("/api/v1/specifications/{spec_id}")
    def specification(spec_id: UUID):
        record = repo().get_specification(spec_id)
        return record | {
            "verifications": repo().list_verifications(spec_id),
            "artifacts": repo().list_artifacts(spec_id),
        }

    @app.get("/api/v1/specifications/{spec_id}/verifications/{run_id}/explanation")
    def explain_verification(spec_id: UUID, run_id: UUID):
        from agent.analyst.formal_feedback import FormalFeedbackExplainer
        from pipeline_spec.models import PipelineSpecification
        from pydantic import ValidationError

        record = repo().get_specification(spec_id)
        run = next(
            (item for item in repo().list_verifications(spec_id) if item["id"] == run_id), None
        )
        if run is None:
            raise PipelineError("NOT_FOUND", "Результат этой проверки не найден.")
        if run["status"] != "FINISHED" or not run["result_json"]:
            raise PipelineError(
                "VERIFICATION_PENDING", "Проверка ещё не завершена.", retryable=True
            )
        data = (record.get("context_json") or {}).get("pipeline_specification")
        if data is None:
            raise PipelineError(
                "CONTEXT_REQUIRED", "Для этой старой версии не сохранена полная спецификация."
            )
        try:
            specification = PipelineSpecification.model_validate(data)
        except ValidationError:
            raise PipelineError(
                "CONTEXT_REQUIRED", "Сохранённая спецификация недоступна для объяснения."
            ) from None
        return FormalFeedbackExplainer().explain(
            run["result_json"], specification, spec_id=str(spec_id), revision=record["version"]
        )

    @app.get("/api/v1/specifications/{spec_id}/verifications/{run_id}/proposals")
    def latest_revision_proposal(spec_id: UUID, run_id: UUID):
        from agent.revision import RevisionService

        return RevisionService(repo(), None).latest(spec_id, run_id)

    @app.post("/api/v1/specifications/{spec_id}/verifications/{run_id}/proposals", status_code=201)
    def propose_revision(spec_id: UUID, run_id: UUID):
        from agent.revision import RevisionService

        return model_call(lambda: RevisionService(repo(), providers()[0]).propose(spec_id, run_id))

    @app.post("/api/v1/specifications/{spec_id}/proposals/{proposal_id}/accept", status_code=201)
    def accept_revision(spec_id: UUID, proposal_id: UUID):
        from agent.revision import RevisionService

        new_id = RevisionService(repo(), None).accept(spec_id, proposal_id)
        return repo().get_specification(new_id)

    @app.post("/api/v1/specifications/{spec_id}/proposals/{proposal_id}/reject")
    def reject_revision(spec_id: UUID, proposal_id: UUID):
        from agent.revision import RevisionService

        RevisionService(repo(), None).reject(spec_id, proposal_id)
        return {"status": "REJECTED"}

    @app.post("/api/v1/specifications/{spec_id}/approve")
    def approve(spec_id: UUID):
        repo().approve_specification(spec_id)
        return repo().get_specification(spec_id)

    @app.post("/api/v1/specifications/{spec_id}/verify", status_code=202)
    def verify(spec_id: UUID):
        record = repo().get_specification(spec_id)
        if record["status"] != "APPROVED":
            raise PipelineError("APPROVAL_REQUIRED", "Сначала согласуйте эту версию спецификации.")
        run_id = repo().enqueue_verification(spec_id)
        return {"id": run_id, "spec_id": spec_id, "status": "QUEUED"}

    @app.post("/api/v1/specifications/{spec_id}/generate")
    def generate(spec_id: UUID, body: GenerationRequest):
        # Enforce the verification gate before touching the provider.
        repo().load_verified_context(spec_id)
        return GenerationService(repo(), PySparkGenerator(LazyGenerationClient())).generate(
            spec_id, body.request_id
        )

    @app.post("/api/v1/specifications/{spec_id}/executions", status_code=202)
    def queue_execution(spec_id: UUID, body: ExecutionRequest):
        from execution.storage import ExecutionStore

        run_id = ExecutionStore(repo()).enqueue(
            spec_id, body.artifact_id, body.inputs, body.request_id, body.parameters
        )
        return ExecutionStore(repo()).get(spec_id, run_id)

    @app.get("/api/v1/specifications/{spec_id}/executions")
    def execution_history(spec_id: UUID):
        from execution.storage import ExecutionStore

        return ExecutionStore(repo()).list(spec_id)

    @app.get("/api/v1/specifications/{spec_id}/executions/{run_id}")
    def execution_status(spec_id: UUID, run_id: UUID):
        from execution.storage import ExecutionStore

        return ExecutionStore(repo()).get(spec_id, run_id)

    @app.get("/api/v1/specifications/{spec_id}/executions/{run_id}/result")
    def execution_result(spec_id: UUID, run_id: UUID):
        from execution.storage import ExecutionStore

        run = ExecutionStore(repo()).get(spec_id, run_id, include_archive=True)
        if run["status"] != "SUCCEEDED" or run["result_archive"] is None:
            raise PipelineError("EXECUTION_NOT_READY", "Результат выполнения ещё недоступен.")
        return Response(
            run["result_archive"],
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=result.zip",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/specifications/{spec_id}/executions/{run_id}/result.csv")
    def execution_csv(spec_id: UUID, run_id: UUID):
        from execution.storage import ExecutionStore

        run = ExecutionStore(repo()).get(spec_id, run_id, include_archive=True)
        if run["status"] != "SUCCEEDED" or run["result_csv"] is None:
            raise PipelineError("EXECUTION_NOT_READY", "CSV-экспорт результата недоступен.")
        return Response(
            run["result_csv"],
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=result.csv",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/v1/specifications/{spec_id}/artifacts/{artifact_id}")
    def download(spec_id: UUID, artifact_id: UUID):
        artifact = repo().get_artifact(spec_id, artifact_id)
        return Response(
            artifact.content,
            media_type="text/x-python; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{artifact.filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


app = create_app()
