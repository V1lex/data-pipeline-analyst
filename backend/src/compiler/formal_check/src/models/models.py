from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class Status(StrEnum):
    SUCCESS = "success"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    STATE_LIMIT_REACHED = "state_limit_reached"
    TIMEOUT = "timeout"


class RunnerOutput(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    status: Status
    timestamp: datetime = Field(default_factory=datetime.now)


class RunnerBatchOutput(BaseModel):
    outputs: list[RunnerOutput]
    elapsed_seconds: float


class CompilationResult(BaseModel):
    model_path: Path
    safety_config_path: Path
    reachable_config_paths: dict[str, Path]
