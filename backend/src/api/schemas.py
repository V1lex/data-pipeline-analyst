from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProjectRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    description: str = ""


class ProjectRenameRequest(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class MessageRequest(BaseModel):
    content: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30000)
    ]
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)


class GenerationRequest(BaseModel):
    request_id: UUID


class SourceSelectionRequest(BaseModel):
    file_ids: list[UUID] = Field(max_length=100)


class ExecutionRequest(BaseModel):
    artifact_id: UUID
    request_id: UUID
    inputs: dict[str, UUID] = Field(min_length=1, max_length=20)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=20)
