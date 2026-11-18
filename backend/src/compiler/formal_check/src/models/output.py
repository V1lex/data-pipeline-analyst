from typing import Literal

from pydantic import BaseModel


class Statistics(BaseModel):
    states_generated: int
    distinct_states: int
    depth: int


class PropertyHolds(BaseModel):
    status: Literal["PROPERTY_HOLDS"] = "PROPERTY_HOLDS"
    engine: Literal["TLC"] = "TLC"
    statistics: Statistics


class Step(BaseModel):
    number: int
    transition: str | None
    state: dict[str, str | bool | int]


class Counterexample(BaseModel):
    steps: list[Step]


class PropertyViolated(BaseModel):
    status: Literal["PROPERTY_VIOLATED"] = "PROPERTY_VIOLATED"
    engine: Literal["TLC"] = "TLC"
    property_id: str
    counterexample: Counterexample


class PropertyUnreachable(BaseModel):
    status: Literal["PROPERTY_UNREACHABLE"] = "PROPERTY_UNREACHABLE"
    engine: Literal["TLC"] = "TLC"
    property_id: str
    error: str


class ValidationErrorItem(BaseModel):
    type: str
    location: str
    message: str


class ModelInvalid(BaseModel):
    status: Literal["MODEL_INVALID"] = "MODEL_INVALID"
    engine: Literal["TLC"] = "TLC"
    errors: list[ValidationErrorItem]


class InfrastructureError(BaseModel):
    status: Literal["INFRASTRUCTURE_ERROR"] = "INFRASTRUCTURE_ERROR"
    engine: Literal["TLC"] = "TLC"
    error: str
