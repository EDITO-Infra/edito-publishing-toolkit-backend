"""Tests for STAC removal routes."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ept.api.dependencies import get_http_client
from ept.api.main import app

from tests.conftest import feature_is_active, integration, require_live_env, unit

# These tests exercise mounted routes, so they skip if the feature is ever
# declared for a later release than EPT_ACTIVE_RELEASE (it ships in 1.0.0).
pytestmark = pytest.mark.skipif(
    not feature_is_active("remove.stac"),
    reason="feature is not mounted in the active release",
)


class RecordingPublishingHttpClient:
    """Record outbound requests and return configured JSON responses.

    Tests use ``body`` for a single response or ``responses`` for an ordered
    sequence. No network request is made.
    """

    def __init__(
        self,
        responses: list[tuple[int, dict[str, Any], dict[str, str]]] | None = None,
        *,
        status_code: int = 202,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body or {"job_id": "job-1", "status": "queued"}
        self.responses = list(responses or [])
        self.error: httpx.RequestError | None = None
        self.calls: list[dict[str, Any]] = []
        self.last_call: dict[str, Any] = {}

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Record one request and return the next configured response."""
        call: dict[str, Any] = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers,
        }
        if params is not None:
            call["params"] = params
        self.last_call = call
        self.calls.append(call)

        if self.error is not None:
            raise self.error
        if self.responses:
            status_code, body, response_headers = self.responses.pop(0)
        else:
            status_code, body, response_headers = self.status_code, self.body, {}
        return httpx.Response(
            status_code,
            json=body,
            headers=response_headers,
            request=httpx.Request(method, url),
        )


@pytest.fixture
def publishing_http_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingPublishingHttpClient]:
    """Inject a stable publishing URL and recording outbound client."""
    fake = RecordingPublishingHttpClient()
    monkeypatch.setenv("PUBLISHING_API_URL", "https://publishing.test")

    async def override_http_client() -> RecordingPublishingHttpClient:
        return fake

    app.dependency_overrides[get_http_client] = override_http_client
    try:
        yield fake
    finally:
        app.dependency_overrides.pop(get_http_client, None)


@unit
def test_remove_stac_translates_request_to_upstream_delete_call(publishing_http_client):
    """Verify EPT translates the public route into an infrastructure deletion job. Ensure that the
    request is sent to the correct URL and includes the necessary auth headers and JSON body."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/remove",
            {"catalog_id": "projects/demo"},
        )
    )
    assert response.status_code == 202

    # Job submission sends auth through headers and keeps the JSON body focused.
    assert publishing_http_client.last_call["method"] == "POST"
    assert publishing_http_client.last_call["url"] == "https://publishing.test/stac/delete"
    assert publishing_http_client.last_call["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer access-secret",
        "X-EDITO-Refresh-Token": "refresh-secret",
    }
    assert publishing_http_client.last_call["json"] == {
        "path": "/catalogs/projects/demo",
        "dry_run": False,
    }


@unit
def test_remove_stac_openapi_documents_body_auth_and_examples():
    """Swagger must show the JSON body and both auth headers needed to remove."""
    schema = app.openapi()
    operation = schema["paths"]["/v1/edito/stac/remove"]["post"]

    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["operationId"] == "queueStacRemovalV1"
    assert operation["tags"] == ["Publish STAC", "remove"]
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RemoveStacRequest"
    }

    refresh_header = _parameter(operation, "X-EDITO-Refresh-Token")
    assert refresh_header["in"] == "header"
    assert refresh_header["required"] is True

    request_schema = schema["components"]["schemas"]["RemoveStacRequest"]
    assert request_schema["additionalProperties"] is False
    catalog_id_schema = request_schema["properties"]["catalog_id"]
    assert catalog_id_schema["examples"] == [
        "projects/project-id/catalog-1",
        "projects/project-id/catalog-1/catalog-2",
    ]
    assert catalog_id_schema["pattern"].startswith("^projects/")
    assert "examples" not in schema["components"]["schemas"]["RemoveStacResponse"]
    assert "application/problem+json" in operation["responses"]["401"]["content"]
    assert "application/problem+json" in operation["responses"]["422"]["content"]
    assert "application/problem+json" in operation["responses"]["502"]["content"]


@unit
def test_remove_stac_validation_problem_uses_json_pointer():
    """Missing request fields should be reported with public JSON Pointers."""
    response = asyncio.run(_post("/v1/edito/stac/remove", {}))

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/invalid-request",
        "title": "Invalid request",
        "status": 422,
        "detail": "The request body is missing required fields.",
        "instance": "/v1/edito/stac/remove",
        "reason": "invalid_request",
        "errors": [{"detail": "Field is required.", "pointer": "/catalog_id"}],
    }


@unit
def test_remove_stac_rejects_non_project_catalog_id(publishing_http_client):
    """Removal outside the projects namespace must not reach upstream."""
    response = asyncio.run(
        _post("/v1/edito/stac/remove", {"catalog_id": "users/alice"})
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"][0]["pointer"] == "/catalog_id"
    assert "projects/" in response.json()["errors"][0]["detail"]
    assert publishing_http_client.calls == []


@unit
@pytest.mark.parametrize(
    "catalog_id",
    [
        "projects/../..",
        "projects/../../../",
        "projects/x/../../../catalogs",
        "projects/proj?dry_run=true",
        "projects/proj#fragment",
        "/projects/absolute",
        "projects/has space",
    ],
)
def test_remove_stac_rejects_path_traversal_in_catalog_id(
    publishing_http_client,
    catalog_id,
):
    """Removal is destructive: nothing that could escape the catalog path may reach upstream."""
    response = asyncio.run(
        _post("/v1/edito/stac/remove", {"catalog_id": catalog_id})
    )

    assert response.status_code == 422
    # The rejection must happen at the EPT boundary: nothing reaches the publisher.
    assert publishing_http_client.calls == []


@unit
def test_remove_stac_rejects_unsupported_request_fields():
    """The public remove request body rejects undocumented caller fields."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/remove",
            {
                "catalog_id": "projects/demo-catalog",
                "unexpected": True,
            },
        )
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "The request body did not match the API schema."
    assert response.json()["errors"] == [{"detail": "Field is not supported.", "pointer": "/unexpected"}]


@unit
def test_remove_stac_rejects_missing_refresh_token_header():
    """Removal job submission needs bearer and refresh-token authentication."""
    response = asyncio.run(
        _post_with_headers(
            "/v1/edito/stac/remove",
            {"catalog_id": "projects/demo"},
            headers={"Authorization": "Bearer valid-token"},
        )
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/refresh-token-required",
        "title": "Refresh token required",
        "status": 401,
        "detail": "Provide X-EDITO-Refresh-Token when submitting a publication or removal job.",
        "instance": "/v1/edito/stac/remove",
        "reason": "missing_refresh_token",
    }


@unit
def test_remove_stac_maps_upstream_rejection(publishing_http_client):
    """Upstream rejection returns a service-neutral, sanitized API problem."""
    publishing_http_client.responses = [
        (401, {"detail": "bearer token not valid", "access_token": "leaked"}, {})
    ]

    response = asyncio.run(_post("/v1/edito/stac/remove", {"catalog_id": "projects/demo"}))

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/publication-request-rejected",
        "title": "Publication request rejected",
        "status": 401,
        "detail": "The publication request could not be accepted.",
        "instance": "/v1/edito/stac/remove",
        "reason": "publication_request_rejected",
        "upstream_response": {
            "detail": "bearer token not valid",
            "access_token": "***REDACTED***",
        },
    }


@unit
def test_remove_stac_maps_upstream_unavailability(publishing_http_client):
    """Connection failures return a service-neutral publication problem."""
    request = httpx.Request("POST", "https://publishing.test/stac/delete")
    publishing_http_client.error = httpx.ConnectError("no route", request=request)

    response = asyncio.run(_post("/v1/edito/stac/remove", {"catalog_id": "projects/demo"}))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/publication-backend-unavailable",
        "title": "Publication backend unavailable",
        "status": 502,
        "detail": "EPT could not complete the publication request because a required service is unavailable.",
        "reason": "publication_backend_unavailable",
        "instance": "/v1/edito/stac/remove",
    }


async def _post(path: str, payload: dict) -> httpx.Response:
    """Send an in-process request to the FastAPI app."""
    return await _post_with_headers(path, payload, headers={"X-EDITO-Refresh-Token": "refresh-secret"})


async def _post_with_headers(path: str, payload: dict, *, headers: dict[str, str]) -> httpx.Response:
    """Send an in-process POST request with explicit headers."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.post(
            path,
            headers=headers,
            json=payload,
        )


def _parameter(operation: dict, name: str) -> dict:
    """Return one OpenAPI operation parameter by name."""
    return next(parameter for parameter in operation["parameters"] if parameter["name"] == name)


@pytest.fixture
def live_catalog_id() -> str:
    """Return the staging project catalog used by removal tests."""
    return require_live_env("REMOVE_PROJECT_CATALOG_ID")


@integration
def test_remove_stac_queues_live_removal_job(
    live_catalog_id: str,
    live_edito_token_pair,
):
    """Queue a real removal job when integration tests are selected."""
    response = asyncio.run(
        _submit_live(
            "/v1/edito/stac/remove",
            {
                "catalog_id": live_catalog_id
            },
            access_token=live_edito_token_pair.access_token,
            refresh_token=live_edito_token_pair.refresh_token,
        )
    )
    assert response.status_code == 202
    assert response.json()["job_id"]
    assert response.json()["status"] == "queued"



async def _submit_live(
    path: str,
    payload: dict,
    *,
    access_token: str,
    refresh_token: str,
) -> httpx.Response:
    """Submit an authenticated request to the in-process EPT API."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=60.0,
    ) as client:
        return await client.post(
            path,
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-EDITO-Refresh-Token": refresh_token,
            },
            json=payload,
        )
