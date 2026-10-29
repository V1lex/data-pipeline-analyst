from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    SUCCESS = "success"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    TIMEOUT = "timeout"


class RunnerOutput(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    status: Status
    timestamp: datetime = Field(default_factory=datetime.now)
