from typing import Annotated, Literal

from pydantic import Field

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.enums import (
    AggregateFunction,
    JoinType,
    NullOrdering,
    SortDirection,
)
from pipeline_spec.models.expressions import BooleanExpression, Expression


class ColumnSelection(SpecBaseModel):
    expr: Expression = Field(description="Column expression or column reference")
    alias: str | None = Field(default=None, description="Output column name if renamed")


class NewColumn(SpecBaseModel):
    name: str = Field(min_length=1, description="New column name")
    expr: Expression = Field(description="Computation expression")


class AggregateDef(SpecBaseModel):
    column: str | Expression | None = Field(
        default=None,
        description="Source column name or expression for aggregation (None for count(*))",
    )
    agg_func: AggregateFunction = Field(description="Aggregation function (count, sum, avg, min, max, etc.)")
    alias: str = Field(min_length=1, description="Name for the resulting aggregate column")


class SortField(SpecBaseModel):
    column: str = Field(min_length=1, description="Column name to sort by")
    direction: SortDirection = Field(default=SortDirection.ASC, description="Sort direction: asc or desc")
    null_ordering: NullOrdering = Field(default=NullOrdering.NULLS_LAST, description="Null ordering")


# --- Operation Definitions ---


class ReadOperation(SpecBaseModel):
    type: Literal["read"] = "read"
    source_id: str = Field(min_length=1, description="ID of the SourceDataset to read")


class FilterOperation(SpecBaseModel):
    type: Literal["filter"] = "filter"
    condition: BooleanExpression = Field(description="Boolean filtering condition")


class SelectOperation(SpecBaseModel):
    type: Literal["select"] = "select"
    columns: list[ColumnSelection] = Field(min_length=1, description="List of columns or expressions to select")


class WithColumnsOperation(SpecBaseModel):
    type: Literal["with_columns"] = "with_columns"
    columns: list[NewColumn] = Field(min_length=1, description="New or modified columns to compute")


class JoinOperation(SpecBaseModel):
    type: Literal["join"] = "join"
    right_input: str = Field(min_length=1, description="ID of right input step or source")
    join_type: JoinType = Field(default=JoinType.INNER, description="Join type: inner, left, right, full, cross, etc.")
    on: list[str] | BooleanExpression = Field(
        description="Join key column names (if matching in both) or explicit boolean join condition"
    )


class AggregateOperation(SpecBaseModel):
    type: Literal["aggregate"] = "aggregate"
    group_by: list[str] = Field(default_factory=list, description="Grouping column names")
    aggregations: list[AggregateDef] = Field(min_length=1, description="List of aggregate function definitions")


class DeduplicateOperation(SpecBaseModel):
    type: Literal["deduplicate"] = "deduplicate"
    subset: list[str] | None = Field(default=None, description="Subset of columns for deduplication, or null for all columns")
    keep: Literal["first", "last"] | None = Field(default="first", description="Which duplicate row to retain")


class SortOperation(SpecBaseModel):
    type: Literal["sort"] = "sort"
    order_by: list[SortField] = Field(min_length=1, description="List of fields to sort by")


class UnionOperation(SpecBaseModel):
    type: Literal["union"] = "union"
    other_inputs: list[str] = Field(min_length=1, description="Other step or source IDs to union with")
    distinct: bool = Field(default=False, description="Whether to remove duplicates after union (union distinct vs union all)")


class WriteOperation(SpecBaseModel):
    type: Literal["write"] = "write"
    sink_id: str = Field(min_length=1, description="ID of the SinkDataset to write to")


type PipelineOperation = Annotated[
    ReadOperation
    | FilterOperation
    | SelectOperation
    | WithColumnsOperation
    | JoinOperation
    | AggregateOperation
    | DeduplicateOperation
    | SortOperation
    | UnionOperation
    | WriteOperation,
    Field(discriminator="type"),
]
