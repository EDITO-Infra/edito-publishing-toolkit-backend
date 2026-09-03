"""Shared fixtures scoped to feature tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ept.api.dependencies import get_http_client, require_edito_bearer_auth
from ept.api.main import app
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from tests.infrastructure.publishing_fakes import RecordingPublishingHttpClient


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


@pytest.fixture
def publishing_http_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingPublishingHttpClient]:
    """Inject a recording outbound HTTP fake into one feature test."""
    fake = RecordingPublishingHttpClient()
    monkeypatch.setenv("PUBLISHING_API_URL", "https://publishing.test")

    async def override_http_client() -> RecordingPublishingHttpClient:
        return fake

    app.dependency_overrides[get_http_client] = override_http_client
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_http_client, None)
