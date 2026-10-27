from collections.abc import Mapping

from pydantic import ValidationError

from src.models.input import (
    BooleanExpression,
    Input,
    Property,
    Transition,
    Variable,
    VariableType,
)
from src.models.models import ModelInvalid, ValidationErrorItem

type ValidationResult = Input | ModelInvalid


def validate_input(data: Mapping[str, object]) -> ValidationResult:
    errors: list[ValidationErrorItem] = []

    try:
        input_model = Input.model_validate(data)
    except ValidationError as exc:
        errors.extend(_pydantic_errors(exc))
        return ModelInvalid(errors=errors)

    variables = {variable.name: variable for variable in input_model.variables}
    errors.extend(_validate_initial_state(input_model.initial, variables))
    errors.extend(_validate_transitions(input_model.transitions, variables))
    errors.extend(_validate_properties(input_model.properties, variables))

    if errors:
        return ModelInvalid(errors=errors)

    return input_model


def _validate_initial_state(
    initial: Mapping[str, object],
    variables: Mapping[str, Variable],
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []

    for name, state in initial.items():
        variable = variables.get(name)

        if variable is None:
            errors.append(
                ValidationErrorItem(
                    type="UNDEFINED_VARIABLE_REFERENCE",
                    location=f"initial.{name}",
                    message=(
                        f"Initial value for variable '{name}' is not defined in variables."
                    ),
                )
            )
            continue

        if variable.type is VariableType.BOOLEAN and not isinstance(state, bool):
            errors.append(
                ValidationErrorItem(
                    type="INVALID_INITIAL_VALUE",
                    location=f"initial.{name}",
                    message=f"Initial value for variable '{name}' must be a boolean.",
                )
            )
        elif variable.type is VariableType.INTEGER and (
            not isinstance(state, int) or isinstance(state, bool)
        ):
            errors.append(
                ValidationErrorItem(
                    type="INVALID_INITIAL_VALUE",
                    location=f"initial.{name}",
                    message=(
                        f"Initial value for variable '{name}' must be an integer."
                    ),
                )
            )
        elif variable.type is VariableType.ENUM and (
            variable.values is None or state not in variable.values
        ):
            errors.append(
                ValidationErrorItem(
                    type="OUT_OF_DOMAIN_VALUE",
                    location=f"initial.{name}",
                    message=(
                        f"Initial value for variable '{name}' must be one of {variable.values}."
                    ),
                )
            )

    return errors


def _validate_transitions(
    transitions: list[Transition],
    variables: Mapping[str, Variable],
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    for index, transition in enumerate(transitions):
        errors.extend(
            _validate_expression(
                transition.guard,
                f"transitions[{index}].guard",
                variables,
            )
        )
    return errors


def _validate_properties(
    properties: list[Property],
    variables: Mapping[str, Variable],
) -> list[ValidationErrorItem]:
    errors: list[ValidationErrorItem] = []
    for index, prop in enumerate(properties):
        errors.extend(
            _validate_expression(
                prop.expression,
                f"properties[{index}].expression",
                variables,
            )
        )
    return errors


def _validate_expression(
    expression: BooleanExpression | None,
    location: str,
    variables: Mapping[str, Variable],
) -> list[ValidationErrorItem]:
    if expression is None:
        return []
    _ = (expression, location, variables)
    return []


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
