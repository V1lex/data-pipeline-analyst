"""Canonical semantic validator shared by API, analyst and worker."""

from compiler.formal_check.src.services.validator import validate_input

__all__ = ["validate_input"]
