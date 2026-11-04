"""Shared pydantic field types for feature request models."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator

__all__ = ["LocalOutput"]


def _not_set(value: Any) -> Any:
    """Coerce falsy JSON placeholders (``false``, ``""``, ``0``) to None.

    Clients (and the handbook examples) send ``"local_output": false`` to mean
    "no local output"; without this coercion those payloads fail validation
    because the field is a ``str | None`` path.
    """
    if isinstance(value, str):
        value = value.strip()
    return value or None


LocalOutput = Annotated[str | None, BeforeValidator(_not_set)]
