from __future__ import annotations

from typing import Any

from pydantic import Field

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.enums import DataType

type ScalarValue = str | int | float | bool | None


class ColumnRef(SpecBaseModel):
    col: str = Field(min_length=1, description="Column name")


class LiteralVal(SpecBaseModel):
    lit: ScalarValue = Field(description="Constant literal value")


# Arithmetic
class AddExpr(SpecBaseModel):
    add: tuple[Expression, Expression]


class SubExpr(SpecBaseModel):
    sub: tuple[Expression, Expression]


class MulExpr(SpecBaseModel):
    mul: tuple[Expression, Expression]


class DivExpr(SpecBaseModel):
    div: tuple[Expression, Expression]


class ModExpr(SpecBaseModel):
    mod: tuple[Expression, Expression]


# Comparisons
class EqExpr(SpecBaseModel):
    eq: tuple[Expression, Expression]


class NeExpr(SpecBaseModel):
    ne: tuple[Expression, Expression]


class LtExpr(SpecBaseModel):
    lt: tuple[Expression, Expression]


class LeExpr(SpecBaseModel):
    le: tuple[Expression, Expression]


class GtExpr(SpecBaseModel):
    gt: tuple[Expression, Expression]


class GeExpr(SpecBaseModel):
    ge: tuple[Expression, Expression]


class InExpr(SpecBaseModel):
    in_: tuple[Expression, list[ScalarValue]] = Field(alias="in")


class BetweenExpr(SpecBaseModel):
    between: tuple[Expression, Expression, Expression]


class IsNullExpr(SpecBaseModel):
    is_null: Expression


class IsNotNullExpr(SpecBaseModel):
    is_not_null: Expression


# Boolean combinators
class AndExpr(SpecBaseModel):
    and_: list[BooleanExpression] = Field(alias="and", min_length=2)


class OrExpr(SpecBaseModel):
    or_: list[BooleanExpression] = Field(alias="or", min_length=2)


class NotExpr(SpecBaseModel):
    not_: tuple[BooleanExpression] = Field(alias="not")


# Cast & Functions
class CastExpr(SpecBaseModel):
    cast: tuple[Expression, DataType]


class WhenCase(SpecBaseModel):
    condition: BooleanExpression
    value: Expression


class WhenExpr(SpecBaseModel):
    when: list[WhenCase] = Field(min_length=1)
    otherwise: Expression | None = None


class FuncCallExpr(SpecBaseModel):
    func: str = Field(min_length=1, description="Built-in function name, e.g. coalesce, concat, lower, trim")
    args: list[Expression] = Field(default_factory=list)


type BooleanComparison = (
    EqExpr
    | NeExpr
    | LtExpr
    | LeExpr
    | GtExpr
    | GeExpr
    | InExpr
    | BetweenExpr
    | IsNullExpr
    | IsNotNullExpr
)

type BooleanExpression = (
    BooleanComparison
    | AndExpr
    | OrExpr
    | NotExpr
    | ColumnRef
    | LiteralVal
)

type ArithmeticExpression = AddExpr | SubExpr | MulExpr | DivExpr | ModExpr

type Expression = (
    ColumnRef
    | LiteralVal
    | BooleanComparison
    | AndExpr
    | OrExpr
    | NotExpr
    | ArithmeticExpression
    | CastExpr
    | WhenExpr
    | FuncCallExpr
    | ScalarValue
)

# Rebuild models for recursive references
for model in (
    ColumnRef,
    LiteralVal,
    AddExpr,
    SubExpr,
    MulExpr,
    DivExpr,
    ModExpr,
    EqExpr,
    NeExpr,
    LtExpr,
    LeExpr,
    GtExpr,
    GeExpr,
    InExpr,
    BetweenExpr,
    IsNullExpr,
    IsNotNullExpr,
    AndExpr,
    OrExpr,
    NotExpr,
    CastExpr,
    WhenCase,
    WhenExpr,
    FuncCallExpr,
):
    model.model_rebuild()
