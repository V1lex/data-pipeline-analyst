from typing import Any

import pytest
from pydantic import ValidationError

from src.models.input import (
    And,
    Eq,
    Input,
    Property,
    PropertyType,
    Set,
    Variable,
    VariableRef,
    VariableType,
)


def valid_contract() -> dict[str, Any]:
    return {
        "variables": [
            {
                "name": "status",
                "type": "enum",
                "values": ["Created", "Approved", "Cancelled"],
            },
            {
                "name": "approved",
                "type": "boolean",
            },
            {
                "name": "attempts",
                "type": "integer",
                "min": 0,
                "max": 3,
            },
        ],
        "initial": {
            "status": "Created",
            "approved": False,
            "attempts": 0,
        },
        "transitions": [
            {
                "name": "Approve",
                "guard": {
                    "eq": [
                        {"var": "status"},
                        "Created",
                    ]
                },
                "effects": [
                    {
                        "set": [
                            "status",
                            "Approved",
                        ]
                    },
                    {
                        "set": [
                            "approved",
                            True,
                        ]
                    },
                ],
            }
        ],
        "properties": [
            {
                "id": "P1",
                "type": "invariant",
                "expression": {
                    "le": [
                        {"var": "attempts"},
                        3,
                    ]
                },
            }
        ],
    }


def test_valid_contract_is_parsed() -> None:
    model = Input.model_validate(valid_contract())

    assert model.variables[0].type is VariableType.ENUM
    assert model.variables[1].type is VariableType.BOOLEAN
    assert model.variables[2].type is VariableType.INTEGER
    assert model.properties[0].type is PropertyType.INVARIANT

    guard = model.transitions[0].guard
    assert isinstance(guard, Eq)

    left_argument = guard.eq[0]
    assert isinstance(left_argument, VariableRef)
    assert left_argument.var == "status"


def test_nested_boolean_expression_is_parsed() -> None:
    model = Input.model_validate(
        {
            **valid_contract(),
            "transitions": [
                {
                    "name": "Approve",
                    "guard": {
                        "and": [
                            {
                                "eq": [
                                    {"var": "status"},
                                    "Created",
                                ]
                            },
                            {
                                "ne": [
                                    {"var": "approved"},
                                    True,
                                ]
                            },
                        ]
                    },
                    "effects": [
                        {
                            "set": [
                                "approved",
                                True,
                            ]
                        }
                    ],
                }
            ],
        }
    )

    guard = model.transitions[0].guard
    assert isinstance(guard, And)
    assert len(guard.and_) == 2


def test_set_effect_is_parsed_with_alias() -> None:
    effect = Set.model_validate(
        {
            "set": [
                "status",
                "Approved",
            ]
        }
    )

    assert effect.set_[0] == "status"
    assert effect.set_[1] == "Approved"


def test_variable_type_is_restricted_to_enum_values() -> None:
    with pytest.raises(ValidationError):
        Variable.model_validate(
            {
                "name": "description",
                "type": "string",
            }
        )


def test_property_type_is_restricted_to_enum_values() -> None:
    with pytest.raises(ValidationError):
        Property.model_validate(
            {
                "id": "P1",
                "type": "safety",
                "expression": {
                    "eq": [
                        {"var": "status"},
                        "Created",
                    ]
                },
            }
        )


def test_enum_variable_requires_values() -> None:
    with pytest.raises(ValidationError, match="requires fields"):
        Variable.model_validate(
            {
                "name": "status",
                "type": "enum",
            }
        )


def test_enum_variable_values_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        Variable.model_validate(
            {
                "name": "status",
                "type": "enum",
                "values": ["Created", "Created"],
            }
        )


def test_boolean_variable_does_not_allow_integer_bounds() -> None:
    with pytest.raises(ValidationError, match="does not allow fields"):
        Variable.model_validate(
            {
                "name": "approved",
                "type": "boolean",
                "min": 0,
            }
        )


def test_integer_variable_requires_min_not_to_exceed_max() -> None:
    with pytest.raises(ValidationError, match="must not be greater"):
        Variable.model_validate(
            {
                "name": "attempts",
                "type": "integer",
                "min": 3,
                "max": 0,
            }
        )


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Variable.model_validate(
            {
                "name": "status",
                "type": "enum",
                "values": ["Created"],
                "description": "Workflow status",
            }
        )


def test_and_requires_at_least_two_expressions() -> None:
    with pytest.raises(ValidationError):
        Input.model_validate(
            {
                **valid_contract(),
                "transitions": [
                    {
                        "name": "Approve",
                        "guard": {
                            "and": [
                                {
                                    "eq": [
                                        {"var": "status"},
                                        "Created",
                                    ]
                                }
                            ]
                        },
                        "effects": [
                            {
                                "set": [
                                    "status",
                                    "Approved",
                                ]
                            }
                        ],
                    }
                ],
            }
        )


def test_duplicate_variable_names_are_rejected() -> None:
    payload = valid_contract()
    payload["variables"].append(
        {
            "name": "status",
            "type": "boolean",
        }
    )

    with pytest.raises(ValidationError, match="Variable names must be unique"):
        Input.model_validate(payload)


def test_duplicate_transition_names_are_rejected() -> None:
    payload = valid_contract()
    payload["transitions"].append(payload["transitions"][0].copy())

    with pytest.raises(
        ValidationError,
        match="Transition names must be unique",
    ):
        Input.model_validate(payload)


def test_duplicate_property_ids_are_rejected() -> None:
    payload = valid_contract()
    payload["properties"].append(payload["properties"][0].copy())

    with pytest.raises(ValidationError, match="Property IDs must be unique"):
        Input.model_validate(payload)


def test_only_one_deadlock_free_property_is_allowed() -> None:
    payload = valid_contract()
    payload["properties"] = [
        {
            "id": "P1",
            "type": "deadlock_free",
            "expression": {
                "eq": [
                    {"var": "status"},
                    "Created",
                ]
            },
        },
        {
            "id": "P2",
            "type": "deadlock_free",
            "expression": {
                "eq": [
                    {"var": "status"},
                    "Approved",
                ]
            },
        },
    ]

    with pytest.raises(
        ValidationError,
        match="At most one property of type 'deadlock_free'",
    ):
        Input.model_validate(payload)
