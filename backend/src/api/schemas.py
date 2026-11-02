from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, StringConstraints

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


class GenerationRequest(BaseModel):
    request_id: UUID
