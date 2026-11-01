from __future__ import annotations

from pydantic import Field, model_validator

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.enums import DataType


class ColumnDefinition(SpecBaseModel):
    name: str = Field(min_length=1, description="Column name")
    data_type: DataType = Field(description="Column data type")
    nullable: bool = Field(default=True, description="Whether column can contain null values")
    description: str | None = Field(default=None, description="Column description or business context")


class DatasetSchema(SpecBaseModel):
    columns: list[ColumnDefinition] = Field(min_length=1, description="List of schema columns")

    @model_validator(mode="after")
    def validate_unique_columns(self) -> DatasetSchema:
        names = [col.name for col in self.columns]
        if len(names) != len(set(names)):
            duplicates = [name for name in names if names.count(name) > 1]
            raise ValueError(f"Duplicate column names in schema: {set(duplicates)}")
        return self
