"""Structural and semantic validation for the canonical finite-state DSL."""

import re
from collections.abc import Mapping

from compiler.formal_check.src.models.input import (
    Add,
    And,
    Eq,
    ExpressionArgument,
    Ge,
    Gt,
    In,
    Input,
    Le,
    Lt,
    Ne,
    Not,
    Or,
    Sub,
    Variable,
    VariableRef,
    VariableType,
)
from compiler.formal_check.src.models.output import ModelInvalid, ValidationErrorItem
from pydantic import ValidationError

type ValidationResult = Input | ModelInvalid

# Generated operators, imported Integers operators, and TLA+ reserved words share
# one identifier namespace with the model's variables/actions/properties.
RESERVED = frozenset(
    """
Init Next Spec vars TypeOK Nat Int Infinity BOOLEAN TRUE FALSE
ACTION ASSUME ASSUMPTION AXIOM BY CASE CHOOSE CONSTANT CONSTANTS COROLLARY
DEF DEFINE DEFS DOMAIN ELSE ENABLED EXCEPT EXTENDS HAVE HIDE IF IN INSTANCE
LAMBDA LEMMA LET LOCAL MODULE NEW OBVIOUS OMITTED ONLY OTHER PICK PROOF
PROPOSITION PROVE QED RECURSIVE SF_ STATE SUBSET SUFFICES TAKE TEMPORAL THEN
THEOREM UNCHANGED UNION USE VARIABLE VARIABLES WF_ WITH WITNESS
""".split()
)


def validate_input(data: Mapping[str, object]) -> ValidationResult:
    try:
        model = Input.model_validate(data)
    except ValidationError as exc:
        return ModelInvalid(errors=_pydantic_errors(exc))
    errors = []
    variables = {variable.name: variable for variable in model.variables}

    def error(location, message, kind="INVALID_EXPRESSION"):
        errors.append(ValidationErrorItem(type=kind, location=location, message=message))

    names = [(v.name, f"variables[{i}].name") for i, v in enumerate(model.variables)]
    names += [(t.name, f"transitions[{i}].name") for i, t in enumerate(model.transitions)]
    names += [(p.id, f"properties[{i}].id") for i, p in enumerate(model.properties)]
    seen = set()
    for name, location in names:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or name in RESERVED:
            error(location, f"'{name}' is not an available TLA+ identifier", "INVALID_IDENTIFIER")
        if name in seen:
            error(location, f"'{name}' is already used in this model", "DUPLICATE_IDENTIFIER")
        seen.add(name)

    for name in variables.keys() - model.initial.keys():
        error(f"initial.{name}", "Every variable needs an initial value", "MISSING_INITIAL_VALUE")
    for name, value in model.initial.items():
        variable = variables.get(name)
        if variable is None:
            error(f"initial.{name}", f"Unknown variable '{name}'", "UNDEFINED_VARIABLE_REFERENCE")
        elif message := _value_error(value, variable):
            error(f"initial.{name}", message, "INVALID_INITIAL_VALUE")

    def expression(expr, location, expected=VariableType.BOOLEAN):
        try:
            actual = _infer(expr, variables)
            if actual is not expected:
                raise ValueError(f"Expected {expected.value}, got {actual.value}")
        except ValueError as exc:
            error(location, str(exc))

    for index, transition in enumerate(model.transitions):
        location = f"transitions[{index}]"
        if transition.guard is not None:
            expression(transition.guard, location + ".guard")
        assigned = set()
        for effect_index, effect in enumerate(transition.effects):
            target, value = effect.set_
            effect_location = f"{location}.effects[{effect_index}]"
            if target in assigned:
                error(
                    effect_location, f"Multiple assignments to '{target}'", "DUPLICATE_ASSIGNMENT"
                )
            assigned.add(target)
            variable = variables.get(target)
            if variable is None:
                error(
                    effect_location,
                    f"Unknown assignment target '{target}'",
                    "UNDEFINED_VARIABLE_REFERENCE",
                )
                continue
            expression(value, effect_location, variable.type)
            if isinstance(value, str | bool | int) and (message := _value_error(value, variable)):
                error(effect_location, message, "OUT_OF_DOMAIN_VALUE")
    for index, prop in enumerate(model.properties):
        expression(prop.expression, f"properties[{index}].expression")
    return ModelInvalid(errors=errors) if errors else model


def _value_error(value: object, variable: Variable) -> str | None:
    if variable.type is VariableType.BOOLEAN:
        return None if isinstance(value, bool) else "Expected a Boolean value"
    if variable.type is VariableType.ENUM:
        return (
            None
            if isinstance(value, str) and value in (variable.values or [])
            else "Value is outside the enum domain"
        )
    if not isinstance(value, int) or isinstance(value, bool):
        return "Expected an integer value"
    if variable.min is not None and value < variable.min:
        return f"Value is below minimum {variable.min}"
    if variable.max is not None and value > variable.max:
        return f"Value is above maximum {variable.max}"
    return None


def _infer(expression: ExpressionArgument, variables: Mapping[str, Variable]) -> VariableType:
    if isinstance(expression, VariableRef):
        if expression.var not in variables:
            raise ValueError(f"Unknown variable reference '{expression.var}'")
        return variables[expression.var].type
    if isinstance(expression, bool):
        return VariableType.BOOLEAN
    if isinstance(expression, int):
        return VariableType.INTEGER
    if isinstance(expression, str):
        return VariableType.ENUM
    if isinstance(expression, In):
        left, items = expression.in_
        kind = _infer(left, variables)
        if any(_infer(item, variables) is not kind for item in items):
            raise ValueError("Membership set elements must have the operand's type")
        return VariableType.BOOLEAN
    if isinstance(expression, And | Or | Not):
        values = (
            expression.and_
            if isinstance(expression, And)
            else expression.or_
            if isinstance(expression, Or)
            else expression.not_
        )
        if any(_infer(value, variables) is not VariableType.BOOLEAN for value in values):
            raise ValueError("Logical operators require Boolean operands")
        return VariableType.BOOLEAN
    if isinstance(expression, Eq | Ne | Lt | Le | Gt | Ge | Add | Sub):
        values = getattr(expression, next(iter(type(expression).model_fields)))
        left, right = (_infer(value, variables) for value in values)
        if isinstance(expression, Eq | Ne):
            if left is not right:
                raise ValueError("Equality operands must have the same type")
            return VariableType.BOOLEAN
        if left is not VariableType.INTEGER or right is not VariableType.INTEGER:
            raise ValueError("Ordering and arithmetic require integer operands")
        return VariableType.INTEGER if isinstance(expression, Add | Sub) else VariableType.BOOLEAN
    raise ValueError(f"Unsupported expression: {type(expression).__name__}")


def _pydantic_errors(exc: ValidationError) -> list[ValidationErrorItem]:
    return [
        ValidationErrorItem(
            type=_pydantic_error_type(error["type"]),
            location=_format_location(error["loc"]),
            message=_clean_message(error["msg"]),
        )
        for error in exc.errors()
    ]


def _format_location(location: tuple[int | str, ...]) -> str:
    result = ""
    for part in location:
        if isinstance(part, int):
            result += f"[{part}]"
        elif result:
            result += f".{part}"
        else:
            result = str(part)
    return result


def _clean_message(message: str) -> str:
    prefix = "Value error, "
    return message.removeprefix(prefix)


def _pydantic_error_type(error_type: str) -> str:
    if error_type == "extra_forbidden":
        return "EXTRA_FIELD"
    if error_type == "missing":
        return "MISSING_FIELD"
    if error_type == "enum":
        return "INVALID_ENUM_VALUE"
    return error_type.upper()
