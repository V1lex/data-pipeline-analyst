import hashlib
import json
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .errors import PipelineError

CHECK_STATUSES = frozenset(
    {
        "PROPERTY_HOLDS",
        "PROPERTY_VIOLATED",
        "MODEL_INVALID",
        "TIMEOUT",
        "STATE_LIMIT_REACHED",
        "ENGINE_ERROR",
        "INFRASTRUCTURE_ERROR",
    }
)


class VerifiedContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    spec_id: UUID
    spec_version: int = Field(ge=1)
    verification_run_id: UUID
    approved_requirements: str
    messages: list[dict[str, Any]]
    behavioral_model: dict[str, Any]
    verification_result: dict[str, Any]
    target_environment: dict[str, Any]

    def fingerprint(self) -> str:
        serialized = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class Completion(BaseModel):
    text: str
    finish_reason: str
    provider: str
    model: str


class ModelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["GENERATED", "NEEDS_CLARIFICATION"]
    answer: str
    filename: str
    content: str
    requirements: list[str]
    notes: list[str]
    questions: list[str]


class GeneratedCode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Old saved manifests predate the explanation field; do not invent a model reply for them.
    answer: str = ""
    filename: str
    content: str
    requirements: list[str]
    notes: list[str]
    provider: str
    model: str
    code_validation: Literal["not_performed"] = "not_performed"


class GeneratedArtifact(GeneratedCode):
    status: Literal["GENERATED"] = "GENERATED"
    artifact_id: UUID
    spec_id: UUID
    spec_version: int
    verification_run_id: UUID
    request_id: UUID
    context_sha256: str


class Artifact(BaseModel):
    id: UUID
    spec_id: UUID
    kind: str
    filename: str
    content: str


def ensure_verified(context: VerifiedContext) -> None:
    verdict = context.verification_result
    statistics = verdict.get("statistics")
    if (
        verdict.get("status") != "PROPERTY_HOLDS"
        or verdict.get("engine") != "TLC"
        or not isinstance(statistics, dict)
        or any(
            type(statistics.get(key)) is not int or statistics[key] < 0
            for key in ("states_generated", "distinct_states", "depth")
        )
    ):
        raise PipelineError(
            "VERIFICATION_REQUIRED", "Нужен общий PROPERTY_HOLDS от TLC для этой версии."
        )
    model = context.behavioral_model
    if (
        not context.approved_requirements.strip()
        or not all(key in model for key in ("variables", "initial", "transitions", "properties"))
        or not model.get("variables")
        or not model.get("properties")
    ):
        raise PipelineError(
            "CONTEXT_REQUIRED", "Не хватает согласованных требований, модели или свойств."
        )
    version = context.target_environment.get("pyspark_version")
    dependencies = context.target_environment.get("extra_dependencies", [])
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)", version)
        or not isinstance(dependencies, list)
        or any(not isinstance(item, str) for item in dependencies)
    ):
        raise PipelineError("CONTEXT_REQUIRED", "Укажите конкретную версию PySpark и зависимости.")
