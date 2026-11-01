from __future__ import annotations

from pydantic import Field, model_validator

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.operations import PipelineOperation, ReadOperation


class PipelineStep(SpecBaseModel):
    id: str = Field(min_length=1, description="Unique identifier of this step")
    name: str = Field(min_length=1, description="Human-readable step name")
    description: str | None = Field(default=None, description="Detailed explanation of this transformation step")
    inputs: list[str] = Field(
        default_factory=list,
        description="List of upstream source IDs or step IDs providing input DataFrames",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Explicit dependency list of step IDs. If not specified, inferred from inputs.",
    )
    operation: PipelineOperation = Field(description="Transformation operation executed by this step")

    @model_validator(mode="after")
    def default_dependencies(self) -> PipelineStep:
        # If depends_on is empty, auto-populate from inputs (excluding pure source IDs)
        if not self.depends_on and self.inputs:
            self.depends_on = list(self.inputs)
        return self
