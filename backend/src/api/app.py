"""Local MVP API. Verification verdicts can only be written by the worker."""

import logging
import os
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

from api.schemas import GenerationRequest, MessageRequest, ProjectRenameRequest, ProjectRequest

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
        from agent.analyst.llm_client import GeminiLLMClient
        from agent.analyst.specification_builder import SpecificationBuilder
        from pipeline_generator.shared_gemini import SharedGeminiClient

        try:
            client = analyst_client or GeminiLLMClient(
                model_name=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
            )
            spec_builder = builder or SpecificationBuilder(
                client=client.client, model_name=client.model_name
            )
            generator = generation_client or SharedGeminiClient.from_analyst_client(client)
            return client, spec_builder, generator
        except ValueError:
            raise PipelineError(
                "CONFIGURATION_ERROR", "Настройте GEMINI_API_KEY и GEMINI_MODEL."
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
            providers()[0].client.close()

    app = FastAPI(title="Data Pipeline Analyst", version="0.2.0", lifespan=lifespan)

    @app.exception_handler(PipelineError)
    async def pipeline_error(request: Request, error: PipelineError):
        status = (
            404 if error.code == "NOT_FOUND" else (502 if error.code.startswith("LLM_") else 409)
        )
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

    @app.post("/api/v1/specifications/{spec_id}/approve")
    def approve(spec_id: UUID):
        from compiler.formal_check.src.models.output import ModelInvalid
        from validator import validate_input

        record = repo().get_specification(spec_id)
        validation = validate_input(record["spec_json"])
        if isinstance(validation, ModelInvalid):
            raise PipelineError(
                "MODEL_INVALID",
                "Исправьте ошибки модели перед согласованием.",
                details={"errors": validation.model_dump(mode="json")["errors"]},
            )
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
