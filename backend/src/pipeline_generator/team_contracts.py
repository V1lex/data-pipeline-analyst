"""Boundary conversions; the upstream packages own their contracts and validation."""

import json
from typing import Any

from pydantic import BaseModel

from .errors import PipelineError


def json_object(
    value: dict[str, Any] | BaseModel | str, *, code: str, exclude_none: bool = False
) -> dict[str, Any]:
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json", by_alias=True, exclude_none=exclude_none)
        elif isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value
    except (ValueError, TypeError, RecursionError):
        raise PipelineError(code, "Ожидался объект по контракту команды.") from None


def specification_snapshot(value: dict[str, Any] | BaseModel) -> tuple[dict, dict | None]:
    """Accept raw canonical Input or extract raw formal_model from PipelineSpecification."""
    if isinstance(value, BaseModel) and hasattr(value, "formal_model"):
        formal = value.formal_model
        if formal is None:
            raise PipelineError("CONTEXT_REQUIRED", "В спецификации отсутствует formal_model.")
        if isinstance(formal, dict):
            raw_formal = formal
        else:
            raw_formal = formal.model_dump(mode="json", by_alias=True)
        return (
            json_object(raw_formal, code="CONTEXT_REQUIRED"),
            json_object(value, code="CONTEXT_REQUIRED"),
        )
    model = json_object(value, code="CONTEXT_REQUIRED")
    if "formal_model" in model:
        raise PipelineError(
            "CONTEXT_REQUIRED",
            "Передайте валидированный PipelineSpecification или Input formal_check.",
        )
    return model, None
