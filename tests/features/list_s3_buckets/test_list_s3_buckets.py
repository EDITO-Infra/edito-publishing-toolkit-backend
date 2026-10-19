"""Tests for the authenticated list-s3-buckets route."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from ept.api.dependencies import require_edito_bearer_auth
from ept.api.main import app
from ept.features.list_s3_buckets.service import list_s3_buckets
from ept.infrastructure.services.edito_auth import (
    EditoBearerAuth,
    S3ConnectionParameters,
)
from tests.conftest import feature_is_active, integration, live_edito_credentials, unit

# These tests exercise mounted routes, so they skip if the feature is ever
# declared for a later release than EPT_ACTIVE_RELEASE (it ships in 1.1.0).
pytestmark = pytest.mark.skipif(
    not feature_is_active("storage.s3_list_buckets"),
    reason="feature is not mounted in the active release",
)


class FakeS3FileSystem:
    """Records call_s3 invocations and answers with one canned payload."""

    def __init__(self, buckets: list[dict[str, Any]]) -> None:
        self._buckets = buckets
        self.calls: list[tuple[str, tuple, dict]] = []

    def call_s3(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, args, kwargs))
        return {"Buckets": self._buckets}


@pytest.fixture
def fake_s3_buckets(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeS3FileSystem]:
    """Route tests never touch real object storage; fake the s3fs filesystem."""
    fake = FakeS3FileSystem([{"Name": "project-demo"}, {"Name": "oidc-alice"}])
    monkeypatch.setattr(
        "ept.features.list_s3_buckets.service.s3_url_to_fs",
        lambda uri, *, storage_options: (fake, "/"),
    )
    yield fake


# Auth comes from the autouse authenticated_api_request fixture in
# tests/features/conftest.py, whose principal carries username "alice".


@unit
def test_list_s3_buckets_route_returns_buckets_and_default_bucket(
    fake_s3_buckets: FakeS3FileSystem,
):
    response = asyncio.run(_post("/v1/storage/s3/buckets", body=_request_body()))

    assert response.status_code == 200
    assert response.json() == {
        "endpoint_url": "https://s3.example.test",
        "buckets": ["oidc-alice", "project-demo"],
        "default_bucket": "oidc-alice",
    }
    assert [call[0] for call in fake_s3_buckets.calls] == ["list_buckets"]


@unit
def test_list_s3_buckets_route_without_username_claim_omits_default_bucket(
    fake_s3_buckets: FakeS3FileSystem,
):
    async def override() -> EditoBearerAuth:
        return EditoBearerAuth(
            subject="sub-anon",
            username=None,
            claims={"sub": "sub-anon"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = override
    try:
        response = asyncio.run(_post("/v1/storage/s3/buckets", body=_request_body()))
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 200
    assert response.json()["default_bucket"] is None


@integration
def test_list_s3_buckets_live_with_sts_credentials(live_edito_credentials):
    """Lists real buckets using credentials minted from EDITO_USERNAME/PASSWORD."""
    from ept.infrastructure.services.edito_auth import exchange_s3_credentials as exchange

    credentials = asyncio.run(
        exchange(live_edito_credentials.username, live_edito_credentials.password)
    )
    params = S3ConnectionParameters.model_validate(credentials.model_dump(exclude={"expiration"}))

    buckets = list_s3_buckets(params)

    assert isinstance(buckets, list)
    assert all(isinstance(bucket, str) and bucket for bucket in buckets)


async def _post(path: str, *, body: dict[str, Any], authorized: bool = True):
    import httpx
    from httpx import ASGITransport

    headers = (
        {"Authorization": "Bearer access-secret"} if authorized else {}
    )
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.test") as client:
        return await client.post(path, json=body, headers=headers)


def _request_body() -> dict[str, Any]:
    return {
        "endpoint_url": "https://s3.example.test",
        "access_key_id": "temporary-key",
        "secret_access_key": "temporary-secret",
        "session_token": "temporary-token",
    }
