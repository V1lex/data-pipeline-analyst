from typing import Any

from pydantic import Field

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.enums import ConstraintLevel, ConstraintType
from pipeline_spec.models.expressions import BooleanExpression


class DataConstraint(SpecBaseModel):
    id: str = Field(min_length=1, description="Unique constraint identifier")
    name: str = Field(min_length=1, description="Descriptive constraint title")
    description: str | None = Field(default=None, description="Detailed business explanation")
    target_step: str = Field(min_length=1, description="Step ID where this constraint must be evaluated")
    columns: list[str] = Field(default_factory=list, description="Columns targeted by this constraint")
    type: ConstraintType = Field(description="Constraint category: not_null, unique, range, in_set, regex_match, custom_sql")
    params: dict[str, Any] = Field(default_factory=dict, description="Parameters specific to constraint type (e.g. min, max, allowed_values)")
    level: ConstraintLevel = Field(default=ConstraintLevel.ERROR, description="Severity level: error (fails pipeline) or warning (alerts)")
    custom_condition: BooleanExpression | None = Field(
        default=None,
        description="Optional custom boolean expression for complex business rules",
    )
