"""Operation registry for the EPT job runner, loaded from ``operations.toml``.

Declarative by design: features never import this feature. Each table names a
feature service (import string) plus its request model; one generic executor
(:meth:`RunOperation.run`) validates the payload, calls the service, and
normalizes the response. Declarations are imported eagerly so a broken entry
fails at startup, not on first submit.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ept.core.contracts import import_object


@dataclass(frozen=True)
class RunOperation:
    """One declared operation executable as an async EPT run."""

    key: str
    service: Any
    request_model: type[BaseModel] | None = None
    endpoint: str | None = None

    async def run(self, payload: dict[str, Any], *, access_token: str | None = None) -> Any:
        """Validate ``payload`` and execute the service; returns a JSON-safe value."""
        request = self.request_model.model_validate(payload) if self.request_model else payload
        result = await self.service(request, access_token=access_token)
        return result.model_dump(mode="json") if isinstance(result, BaseModel) else result


def _load_operations() -> dict[str, RunOperation]:
    """Build the operation registry from ``operations.toml``."""
    manifest = Path(__file__).with_name("operations.toml")
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    operations: dict[str, RunOperation] = {}
    for table in data.get("operations", {}).values():
        service = import_object(str(table["service"]))
        if not callable(service):
            raise TypeError(f"Service '{table['service']}' is not callable.")
        model = import_object(str(table["request_model"])) if table.get("request_model") else None
        if model is not None and not (
            isinstance(model, type) and issubclass(model, BaseModel)
        ):
            raise TypeError(f"Request model '{table['request_model']}' is not a BaseModel.")
        operation = RunOperation(
            key=str(table["key"]),
            service=service,
            request_model=model,
            endpoint=str(table["endpoint"]) if table.get("endpoint") else None,
        )
        operations[operation.key] = operation
    return operations


OPERATIONS: dict[str, RunOperation] = _load_operations()


def registered_operations() -> dict[str, RunOperation]:
    """Return the declared operation registry (feature entrypoint)."""
    return OPERATIONS


def resolve_operation(key: str) -> RunOperation | None:
    return OPERATIONS.get(key)


def validate_payload(operation: RunOperation, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a submit payload against the operation's declared request model.

    Returns the model-dumped payload; runners re-validate defensively. Raises
    :class:`pydantic.ValidationError` (mapped to 422 by the route layer).
    """
    if operation.request_model is None:
        return payload
    model = operation.request_model.model_validate(payload)
    dumped = model.model_dump(mode="json")
    return dumped if isinstance(dumped, dict) else {"value": dumped}
