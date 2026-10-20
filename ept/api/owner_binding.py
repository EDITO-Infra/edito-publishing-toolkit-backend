"""Instance-to-owner binding: the first login locks the deployment to that user.

The first successful authentication — a password grant, a refresh grant, or a
protected request with a valid bearer token — binds this EPT instance to that
EDITO subject (``sub``). Any later authentication from a different subject is
rejected with ``403 instance_bound``. The binding lives in a single state file
holding the sha256 of the subject (never the raw subject) plus the username
and timestamp for operator support. Operators re-bind by deleting the state
file and restarting; mount ``data/`` on a persistent volume so the binding
survives restarts on ephemeral filesystems.

honey: sha256, not an HMAC — host-filesystem tampering is out of the threat
model (anyone with filesystem access can delete the state file and re-claim);
this file only needs to identify the bound owner, not protect a secret.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import jwt

logger = logging.getLogger(__name__)

INSTANCE_BOUND = "instance_bound"
"""Reason returned when a request's subject is not the bound owner."""


def _state_path() -> Path:
    """Return the owner-binding state file path."""
    return Path(os.getenv("EPT_OWNER_STATE_PATH", "data/owner.state"))


def check_owner(subject: str | None, username: str | None = None) -> str | None:
    """Return ``INSTANCE_BOUND`` when subject may not use this instance, else ``None``.

    An unclaimed instance is claimed by the first authenticated subject.
    A ``None`` subject (pathological Keycloak response) is allowed through
    without claiming so a malformed token cannot brick the binding.
    """
    if not subject:
        logger.warning("Owner binding skipped reason=missing_subject")
        return None

    bound = _read_binding()
    if bound is None:
        _write_binding(subject, username)
        logger.info("Instance bound to owner username=%s", username or "-")
        return None
    if bound["sub_sha256"] == _hash(subject):
        return None
    logger.warning(
        "Request rejected reason=%s requester_username=%s owner_username=%s",
        INSTANCE_BOUND,
        username or "-",
        bound.get("username") or "-",
    )
    return INSTANCE_BOUND


def subject_from_token(token: str) -> tuple[str | None, str | None]:
    """Extract ``(sub, preferred_username)`` from a freshly received token.

    The token arrives directly from Keycloak over TLS, so the payload is read
    without re-verifying the signature; signature validation still happens on
    every protected route via :func:`validate_bearer_token`.
    """
    try:
        claims = jwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=["RS256", "RS512"],
        )
    except jwt.PyJWTError:
        logger.warning("Owner binding skipped reason=token_unreadable")
        return None, None
    subject = claims.get("sub")
    username = claims.get("preferred_username")
    return (
        subject if isinstance(subject, str) and subject else None,
        username if isinstance(username, str) and username else None,
    )


def _hash(subject: str) -> str:
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def _read_binding() -> dict | None:
    """Return the stored binding record, or ``None`` when unclaimed or unreadable.

    An unreadable or malformed state file is treated as unclaimed and the next
    login re-claims it: a corrupt file must not lock the deployment permanently.
    """
    try:
        record = json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning(
            "Owner binding state unreadable path=%s; treating instance as unclaimed",
            _state_path(),
        )
        return None
    if isinstance(record, dict) and isinstance(record.get("sub_sha256"), str) and record["sub_sha256"]:
        return record
    return None


def _write_binding(subject: str, username: str | None) -> None:
    """Atomically write the binding record."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "sub_sha256": _hash(subject),
        "username": username or "",
        "bound_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    # honey: assumes a single uvicorn worker per deployment (the shipped
    # deployment does); concurrent claims are last-writer-wins, not corrupt.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".owner-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle)
        os.replace(tmp, path)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
