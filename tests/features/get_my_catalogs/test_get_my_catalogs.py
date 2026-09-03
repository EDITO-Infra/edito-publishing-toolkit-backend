"""Tests for the authenticated get-my-catalogs route."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ept.api.dependencies import get_http_client, require_edito_bearer_auth
from ept.api.main import app
from ept.features.get_my_catalogs.adapters import from_edito_stac_catalogs
from ept.features.get_my_catalogs.models import GetMyCatalogsResponse
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from ept.infrastructure.services.edito_stac_api import EditoStacUserCatalogs
from tests.conftest import integration, unit


CATALOG_PAYLOAD = [
    {
        "id": "projects/demo",
        "title": "Demo",
        "description": "Demo catalog",
        "links": [],
        "level": 2,
        "counters": {"total": 0, "collections": []},
        "owner": "alice",
        "visibility": ["public"],
        "created": "2026-08-01T10:00:00Z",
        "rtype": None,
        "stac_url": None,
        "pinned": False,
        "future_catalog_field": "preserved",
    },
    {
        "id": "users/alice",
        "title": None,
        "description": "Personal catalog",
        "links": [],
        "level": 2,
        "counters": {"total": 1, "collections": []},
        "owner": "alice",
        "visibility": ["private"],
        "created": "2026-08-02T10:00:00Z",
        "rtype": None,
        "stac_url": None,
        "pinned": True,
    },
]


class CatalogHttpClient:
    """Controllable HTTP fake injected through the shared client dependency."""

    def __init__(self) -> None:
        self.status_code = 200
        self.payload: Any = CATALOG_PAYLOAD
        self.error: httpx.RequestError | None = None
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Record and resolve one outbound request."""
        self.calls.append({"method": method, "url": url, "headers": headers})
        request = httpx.Request(method, url)
        if self.error is not None:
            raise self.error
        return httpx.Response(
            self.status_code,
            json=self.payload,
            request=request,
        )


@pytest.fixture
def catalog_http_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[CatalogHttpClient]:
    """Inject a stable EDITO STAC URL and recording outbound client."""
    fake = CatalogHttpClient()
    monkeypatch.setenv("EDITO_STAC_API_URL", "https://stac.test/data")

    async def override_http_client() -> CatalogHttpClient:
        return fake

    app.dependency_overrides[get_http_client] = override_http_client
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_http_client, None)


@unit
def test_get_my_catalogs_response_filters_non_project_catalogs():
    """The feature response exposes only IDs beginning with ``projects/``."""
    catalogs = EditoStacUserCatalogs.model_validate(
        [
            *CATALOG_PAYLOAD,
            {**CATALOG_PAYLOAD[0], "id": "archive/projects/demo"},
        ]
    )

    response = from_edito_stac_catalogs(catalogs)

    assert response.root == ["projects/demo"]


@unit
def test_get_my_catalogs_returns_project_catalog_ids(catalog_http_client):
    """The public route omits catalogs outside the projects namespace."""
    response = asyncio.run(_get("/v1/edito/stac/mycatalogs"))

    assert response.status_code == 200
    assert response.json() == ["projects/demo"]
    assert catalog_http_client.calls == [
        {
            "method": "GET",
            "url": "https://stac.test/data/users/alice/catalogs",
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer access-secret",
            },
        }
    ]


@unit
def test_get_my_catalogs_requires_username_claim(catalog_http_client):
    """A token without preferred_username fails before an upstream request."""

    async def principal_without_username() -> EditoBearerAuth:
        return EditoBearerAuth(
            subject="test-user",
            username=None,
            claims={"sub": "test-user"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = principal_without_username
    try:
        response = asyncio.run(_get("/v1/edito/stac/mycatalogs"))
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/invalid-bearer-token",
        "title": "Invalid bearer token",
        "status": 401,
        "detail": "The bearer token is not valid.",
        "instance": "/v1/edito/stac/mycatalogs",
        "reason": "invalid_bearer_token",
    }
    assert catalog_http_client.calls == []


@unit
def test_get_my_catalogs_wraps_upstream_error_response(catalog_http_client):
    """An upstream error keeps its status and receives the EDITO STAC envelope."""
    catalog_http_client.status_code = 404
    catalog_http_client.payload = {
        "ErrorCode": 404,
        "ErrorMessage": "Not Found",
        "access_token": "leaked",
    }

    response = asyncio.run(_get("/v1/edito/stac/mycatalogs"))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-error-response",
        "title": "EDITO STAC API error response",
        "status": 404,
        "detail": "The EDITO STAC API returned an error response.",
        "instance": "/v1/edito/stac/mycatalogs",
        "reason": "edito_stac_api_error_response",
        "upstream_response": {
            "ErrorCode": 404,
            "ErrorMessage": "Not Found",
            "access_token": "***REDACTED***",
        },
    }


@unit
def test_get_my_catalogs_maps_upstream_unavailability(catalog_http_client):
    """A connection failure returns a stable 502 problem."""
    request = httpx.Request("GET", "https://stac.test/data/users/alice/catalogs")
    catalog_http_client.error = httpx.ConnectError("no route", request=request)

    response = asyncio.run(_get("/v1/edito/stac/mycatalogs"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-unavailable",
        "title": "EDITO STAC API unavailable",
        "status": 502,
        "detail": "EPT could not retrieve catalogs because the EDITO STAC API is unavailable.",
        "reason": "edito_stac_api_unavailable",
        "instance": "/v1/edito/stac/mycatalogs",
    }


@unit
def test_get_my_catalogs_openapi_contract():
    """OpenAPI documents the direct array response and bearer requirement."""
    schema = app.openapi()
    operation = schema["paths"]["/v1/edito/stac/mycatalogs"]["get"]

    assert operation["operationId"] == "getMyCatalogsV1"
    assert operation["summary"] == "Get my catalogs"
    assert operation["tags"] == ["STAC Catalogs"]
    assert operation["security"] == [{"HTTPBearer": []}]
    assert "requestBody" not in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GetMyCatalogsResponse"
    }
    response_schema = schema["components"]["schemas"]["GetMyCatalogsResponse"]
    assert response_schema["type"] == "array"
    assert response_schema["items"] == {"type": "string"}
    assert "application/problem+json" in operation["responses"]["401"]["content"]
    assert "application/problem+json" in operation["responses"]["502"]["content"]


@integration
def test_get_my_catalogs_live(
    live_edito_credentials,
    live_edito_token_pair,
):
    """The feature route returns the live EDITO user-catalog IDs."""

    async def live_principal() -> EditoBearerAuth:
        """Reuse the separately validated live token at the feature boundary."""
        return EditoBearerAuth(
            subject="live-edito-user",
            username=live_edito_credentials.username,
            claims={"preferred_username": live_edito_credentials.username},
            access_token=live_edito_token_pair.access_token,
        )

    app.dependency_overrides[require_edito_bearer_auth] = live_principal
    try:
        response = asyncio.run(
            _get(
                "/v1/edito/stac/mycatalogs",
                headers={"Authorization": "Bearer live-token-validated-by-fixture"},
                timeout=60.0,
            )
        )
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 200, response.text
    catalogs = GetMyCatalogsResponse.model_validate(response.json())
    assert all(catalog_id.startswith("projects/") for catalog_id in catalogs.root)


async def _get(
    path: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> httpx.Response:
    """Send an in-process GET request to the EPT application."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=timeout,
    ) as client:
        return await client.get(path, headers=headers)
