from __future__ import annotations

from enum import StrEnum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VariableType(StrEnum):
    ENUM = "enum"
    BOOLEAN = "boolean"
    INTEGER = "integer"


class PropertyType(StrEnum):
    INVARIANT = "invariant"
    FORBIDDEN_STATE = "forbidden_state"
    REACHABLE = "reachable"
    DEADLOCK_FREE = "deadlock_free"


class VariableTypeRule(TypedDict):
    required_fields: frozenset[str]
    allowed_fields: frozenset[str]


VARIABLE_TYPE_RULES: dict[VariableType, VariableTypeRule] = {
    VariableType.ENUM: {
        "required_fields": frozenset({"values"}),
        "allowed_fields": frozenset({"name", "type", "values"}),
    },
    VariableType.BOOLEAN: {
        "required_fields": frozenset(),
        "allowed_fields": frozenset({"name", "type"}),
    },
    VariableType.INTEGER: {
        "required_fields": frozenset(),
        "allowed_fields": frozenset({"name", "type", "min", "max"}),
    },
}

type Scalar = str | bool | int


class InputBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VariableRef(InputBaseModel):
    var: str = Field(min_length=1)


# ---------- Boolean expressions ----------


class Eq(InputBaseModel):
    eq: tuple[ExpressionArgument, ExpressionArgument]


class Ne(InputBaseModel):
    ne: tuple[ExpressionArgument, ExpressionArgument]


class Lt(InputBaseModel):
    lt: tuple[ExpressionArgument, ExpressionArgument]


class Le(InputBaseModel):
    le: tuple[ExpressionArgument, ExpressionArgument]


class Gt(InputBaseModel):
    gt: tuple[ExpressionArgument, ExpressionArgument]


class Ge(InputBaseModel):
    ge: tuple[ExpressionArgument, ExpressionArgument]


class In(InputBaseModel):
    in_: tuple[ExpressionArgument, list[Scalar]] = Field(alias="in")


class And(InputBaseModel):
    and_: tuple[BooleanExpression, ...] = Field(
        alias="and",
        min_length=2,
    )


class Or(InputBaseModel):
    or_: tuple[BooleanExpression, ...] = Field(
        alias="or",
        min_length=2,
    )


class Not(InputBaseModel):
    not_: tuple[BooleanExpression] = Field(alias="not")


# ---------- Arithmetic expressions ----------


class Add(InputBaseModel):
    add: tuple[ExpressionArgument, ExpressionArgument]


class Sub(InputBaseModel):
    sub: tuple[ExpressionArgument, ExpressionArgument]


type BooleanExpression = Eq | Ne | Lt | Le | Gt | Ge | In | And | Or | Not
type Expression = BooleanExpression | Add | Sub
type ExpressionArgument = Scalar | VariableRef | Expression


# ---------- Effects ----------


class Set(InputBaseModel):
    set_: tuple[str, ExpressionArgument] = Field(alias="set")


type Effect = Set

for model in (
    Eq,
    Ne,
    Lt,
    Le,
    Gt,
    Ge,
    In,
    And,
    Or,
    Not,
    Add,
    Sub,
    Set,
):
    model.model_rebuild()


# ---------- Contract models ----------


class Variable(InputBaseModel):
    name: str = Field(min_length=1)
    type: VariableType
    values: list[str] | None = None
    min: int | None = None
    max: int | None = None

    @model_validator(mode="after")
    def validate_type_rules(self) -> Variable:
        rules = VARIABLE_TYPE_RULES[self.type]

        provided_fields = {
            name for name, value in self.model_dump().items() if value is not None
        }

        missing_fields = rules["required_fields"] - provided_fields
        if missing_fields:
            raise ValueError(
                f"Variable type '{self.type}' requires fields: "
                f"{', '.join(sorted(missing_fields))}"
            )

        unsupported_fields = provided_fields - rules["allowed_fields"]
        if unsupported_fields:
            raise ValueError(
                f"Variable type '{self.type}' does not allow fields: "
                f"{', '.join(sorted(unsupported_fields))}"
            )

        if self.type is VariableType.ENUM:
            if not self.values:
                raise ValueError("Enum variable must contain at least one value")

            if len(self.values) != len(set(self.values)):
                raise ValueError("Enum variable values must be unique")

        if (
            self.type is VariableType.INTEGER
            and self.min is not None
            and self.max is not None
            and self.min > self.max
        ):
            raise ValueError("Variable 'min' must not be greater than 'max'")

        return self


class Transition(InputBaseModel):
    name: str = Field(min_length=1)
    guard: BooleanExpression
    effects: list[Effect] = Field(min_length=1)


class Property(InputBaseModel):
    id: str = Field(min_length=1)
    type: PropertyType
    expression: BooleanExpression


class Input(InputBaseModel):
    variables: list[Variable] = Field(min_length=1)
    initial: dict[str, Scalar]
    transitions: list[Transition]
    properties: list[Property]

    @model_validator(mode="after")
    def validate_contract(self) -> Input:
        self._ensure_unique(
            "variable names",
            [variable.name for variable in self.variables],
        )
        self._ensure_unique(
            "transition names",
            [transition.name for transition in self.transitions],
        )
        self._ensure_unique(
            "property IDs",
            [prop.id for prop in self.properties],
        )

        deadlock_free_count = sum(
            prop.type is PropertyType.DEADLOCK_FREE for prop in self.properties
        )

        if deadlock_free_count > 1:
            raise ValueError("At most one property of type 'deadlock_free' is allowed")

        return self

    @staticmethod
    def _ensure_unique(label: str, values: list[str]) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"{label[0].upper()}{label[1:]} must be unique")
