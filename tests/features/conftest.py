"""Shared fixtures scoped to feature tests."""

from __future__ import annotations

import pytest

import ept.infrastructure.utils.urlguard as urlguard
from ept.api.dependencies import get_http_client, require_edito_bearer_auth
from ept.api.main import app
from ept.infrastructure.services.edito_auth import EditoBearerAuth


@pytest.fixture(autouse=True)
def fake_public_dns(request, monkeypatch):
    """Resolve every hostname to a public IP in unit tests.

    The SSRF guard resolves DNS before fetching; unit tests drive MockTransport
    with fake hostnames, so real resolution is neither wanted nor available.
    Integration tests keep real DNS for live services.
    """
    if "integration" in request.keywords:
        yield
        return

    async def public_resolution(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(urlguard, "_resolve", public_resolution)
    yield


@pytest.fixture(autouse=True)
def authenticated_api_request(request):
    """Use a stable principal for feature unit tests.

    Integration tests retain the real authentication path and pass live tokens explicitly.
    """
    if "integration" in request.keywords:
        yield
        return

    async def authenticated_principal() -> EditoBearerAuth:
        return EditoBearerAuth(
            subject="test-user",
            username="alice",
            claims={"sub": "test-user", "preferred_username": "alice"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = authenticated_principal
    yield
    app.dependency_overrides.pop(require_edito_bearer_auth, None)
