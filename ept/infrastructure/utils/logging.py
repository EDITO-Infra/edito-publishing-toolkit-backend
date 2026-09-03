"""Logging helpers that remove credential material from diagnostic payloads."""

from __future__ import annotations

from typing import Any
import os
from urllib.parse import parse_qsl, urlencode


SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "bearer_token",
    "password",
    "preferred_username",
    "refresh_token",
    "token",
    "username",
}


def sanitize_payload(value: Any) -> Any:
    """Recursively redact known sensitive keys in structured payloads.

    Redaction is key-based and does not make arbitrary text safe. Callers should
    avoid exposing unstructured upstream bodies, URLs with signed query strings,
    or other strings that may embed credentials.
    """
    if isinstance(value, dict):
        # Pydantic errors store the rejected value under ``input`` and identify
        # its field separately in ``loc``; use that location to redact it too.
        location = value.get("loc")
        redact_input = (
            isinstance(location, (list, tuple))
            and bool(location)
            and str(location[-1]).lower() in SENSITIVE_KEYS
        )
        return {
            key: (
                "***REDACTED***"
                if str(key).lower() in SENSITIVE_KEYS or (redact_input and key == "input")
                else sanitize_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_payload(item) for item in value)
    return value


def sanitize_query_string(query: str) -> str:
    """Redact credential-like query-string values while preserving context."""
    return urlencode(
        [
            (key, "***REDACTED***" if key.lower() in SENSITIVE_KEYS else value)
            for key, value in parse_qsl(query, keep_blank_values=True)
        ]
    )


def token_diagnostic(token: str | None) -> dict[str, Any]:
    """Describe token presence and length without exposing it by default.

    ``EPT_LOG_TOKEN_VALUES`` enables full values for local troubleshooting, but
    that mode is unsafe for shared or persistent logs and must not be enabled in
    deployed environments.
    """
    if not token:
        return {"present": False}
    diagnostic: dict[str, Any] = {"present": True, "length": len(token)}
    if os.getenv("EPT_LOG_TOKEN_VALUES", "").lower() in {"1", "true", "yes", "on"}:
        diagnostic["value"] = token
    return diagnostic
