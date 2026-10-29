from typing import Literal

from pydantic import BaseModel, Field


class Statistics(BaseModel):
    states_generated: int
    distinct_states: int
    depth: int


class PropertyHolds(BaseModel):
    status: str = Field(default="PROPERTY_HOLDS", frozen=True)
    engine: str = Field(default="TLC")
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


class ValidationErrorItem(BaseModel):
    type: str
    location: str
    message: str


class ModelInvalid(BaseModel):
    status: str = Field(default="MODEL_INVALID", frozen=True)
    engine: str = Field(default="TLC")
    errors: list[ValidationErrorItem]
