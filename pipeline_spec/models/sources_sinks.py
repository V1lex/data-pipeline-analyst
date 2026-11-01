from __future__ import annotations

from pydantic import Field

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.enums import FileFormat, SaveMode
from pipeline_spec.models.schema import DatasetSchema


class SourceDataset(SpecBaseModel):
    id: str = Field(min_length=1, description="Unique identifier of input dataset (e.g. raw_orders)")
    name: str = Field(min_length=1, description="Human-readable dataset name")
    description: str | None = Field(default=None, description="Description of the input data")
    format: FileFormat = Field(description="Storage format (csv, parquet, delta, etc.)")
    location: str = Field(min_length=1, description="Storage URI or table path")
    schema_: DatasetSchema | None = Field(default=None, alias="schema", description="Explicit schema definition")
    options: dict[str, str] = Field(default_factory=dict, description="Format-specific read options (e.g. delimiter, header)")
    partition_columns: list[str] = Field(default_factory=list, description="Partition columns if partitioned")


class SinkDataset(SpecBaseModel):
    id: str = Field(min_length=1, description="Unique identifier of output target")
    name: str = Field(min_length=1, description="Human-readable sink name")
    description: str | None = Field(default=None, description="Description of the output target")
    from_step: str = Field(min_length=1, description="ID of the pipeline step that produces data for this sink")
    format: FileFormat = Field(description="Storage format for output (parquet, delta, etc.)")
    location: str = Field(min_length=1, description="Destination path or table name")
    save_mode: SaveMode = Field(default=SaveMode.OVERWRITE, description="Save mode: overwrite, append, etc.")
    partition_by: list[str] = Field(default_factory=list, description="Columns to partition output data by")
    options: dict[str, str] = Field(default_factory=dict, description="Format-specific write options")
