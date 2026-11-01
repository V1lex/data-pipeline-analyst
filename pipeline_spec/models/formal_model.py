from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator

from pipeline_spec.models.base import SpecBaseModel
from pipeline_spec.models.expressions import BooleanExpression

# Ensure formal_check is discoverable if present in repository
REPO_ROOT = Path(__file__).resolve().parents[2]
FORMAL_CHECK_PATH = REPO_ROOT / "formal_check"
if FORMAL_CHECK_PATH.exists() and str(FORMAL_CHECK_PATH) not in sys.path:
    sys.path.insert(0, str(FORMAL_CHECK_PATH))


class FormalVariable(SpecBaseModel):
    name: str = Field(min_length=1)
    type: str = Field(description="'enum', 'boolean', or 'integer'")
    values: list[str] | None = Field(default=None, description="Allowed values if type is enum")
    min: int | None = Field(default=None, description="Minimum integer value")
    max: int | None = Field(default=None, description="Maximum integer value")


class FormalEffect(SpecBaseModel):
    set_: tuple[str, Any] = Field(alias="set", description="Variable name and new value expression")


class FormalTransition(SpecBaseModel):
    name: str = Field(min_length=1)
    guard: BooleanExpression | dict[str, Any] | None = None
    effects: list[FormalEffect] = Field(min_length=1)


class FormalProperty(SpecBaseModel):
    id: str = Field(min_length=1)
    type: str = Field(description="'invariant', 'forbidden_state', 'reachable', or 'deadlock_free'")
    expression: BooleanExpression | dict[str, Any]


class PipelineFormalModel(SpecBaseModel):
    variables: list[FormalVariable] = Field(min_length=1)
    initial: dict[str, Any]
    transitions: list[FormalTransition]
    properties: list[FormalProperty]

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> PipelineFormalModel:
        var_names = [v.name for v in self.variables]
        if len(var_names) != len(set(var_names)):
            raise ValueError("Formal model variable names must be unique")

        trans_names = [t.name for t in self.transitions]
        if len(trans_names) != len(set(trans_names)):
            raise ValueError("Formal model transition names must be unique")

        prop_ids = [p.id for p in self.properties]
        if len(prop_ids) != len(set(prop_ids)):
            raise ValueError("Formal model property IDs must be unique")

        return self

    def to_formal_check_input(self) -> Any:
        """Converts to formal_check.src.models.input.Input instance."""
        try:
            from src.models.input import Input as FormalCheckInput

            return FormalCheckInput.model_validate(self.model_dump(by_alias=True, exclude_none=True))
        except ImportError:
            # Try importing with package prefix if run outside formal_check dir
            from formal_check.src.models.input import Input as FormalCheckInput

            return FormalCheckInput.model_validate(self.model_dump(by_alias=True, exclude_none=True))
