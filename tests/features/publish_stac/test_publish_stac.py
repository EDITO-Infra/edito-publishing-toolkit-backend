"""Tests for STAC publication routes."""

import asyncio

import httpx
import pytest

from ept.api.dependencies import get_http_client
from ept.api.main import app
from tests.conftest import integration, require_live_env, unit


@unit
def test_publish_stac_translates_request_to_upstream_publish_call(publishing_http_client):
    """Verify EPT sends the infrastructure request body and auth headers.

    The test calls the public EPT route, not the service function directly. That
    covers FastAPI request parsing, dependency injection, and response rendering,
    while the fake HTTP client keeps the test fast and deterministic.
    """
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": "https://example.test/catalog.json",
                "catalog_id": "projects/demo",
            },
        )
    )
    assert response.status_code == 202
    assert response.json() == {"job_id": "job-1", "status": "queued"}

    # ``publishing_http_client.last_call`` is the infrastructure request EPT would send.
    assert publishing_http_client.last_call["method"] == "POST"
    assert publishing_http_client.last_call["url"] == "https://publishing.test/stac/publish"
    assert publishing_http_client.last_call["headers"] == {
        "Accept": "application/json",
        "Authorization": "Bearer access-secret",
        "X-EDITO-Refresh-Token": "refresh-secret",
    }
    assert publishing_http_client.last_call["json"] == {
        "remote_stac_url": "https://example.test/catalog.json",
        "parent_path": "/catalogs/projects/demo",
        "dry_run": False,
        "overwrite": True,
    }


@unit
def test_publish_stac_rejects_missing_refresh_token_header():
    """Publication job submission needs bearer and refresh-token authentication."""
    response = asyncio.run(
        _post_with_headers(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": "https://example.test/catalog.json",
                "catalog_id": "projects/demo",
            },
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
        "instance": "/v1/edito/stac/publish",
        "reason": "missing_refresh_token",
    }


@unit
def test_publish_stac_openapi_documents_body_auth_and_examples():
    """Swagger must show the JSON body and both auth headers needed to publish."""
    schema = app.openapi()
    operation = schema["paths"]["/v1/edito/stac/publish"]["post"]

    assert operation["security"] == [{"HTTPBearer": []}]
    assert operation["operationId"] == "queueStacPublicationV1"
    assert operation["tags"] == ["STAC Publication and Removal"]
    assert operation["requestBody"]["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/PublishStacRequest"
    }

    refresh_header = _parameter(operation, "X-EDITO-Refresh-Token")
    assert refresh_header["in"] == "header"
    assert refresh_header["required"] is True

    request_schema = schema["components"]["schemas"]["PublishStacRequest"]
    assert request_schema["additionalProperties"] is False
    assert request_schema["properties"]["remote_stac_url"]["examples"] == [
        "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json",
        "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog-2/catalog.json",
    ]
    catalog_id_schema = request_schema["properties"]["catalog_id"]
    assert catalog_id_schema["examples"] == [
        "projects/project-id",
        "projects/project-id/catalog-1",
    ]
    assert catalog_id_schema["pattern"].startswith("^projects/")
    assert "examples" not in schema["components"]["schemas"]["PublishStacResponse"]
    assert "application/problem+json" in operation["responses"]["401"]["content"]
    assert "application/problem+json" in operation["responses"]["422"]["content"]
    assert "application/problem+json" in operation["responses"]["502"]["content"]


@unit
def test_publish_stac_validation_problem_uses_json_pointer():
    """This test ensures that the FastAPI validation error response uses JSON Pointer in the 
    errors list to indicate which field is missing or invalid, as per the OpenAPI specification."""
    response = asyncio.run(
        _post("/v1/edito/stac/publish", {"catalog_id": "projects/demo"})
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/invalid-request",
        "title": "Invalid request",
        "status": 422,
        "detail": "The request body is missing required fields.",
        "instance": "/v1/edito/stac/publish",
        "reason": "invalid_request",
        "errors": [{"detail": "Field is required.", "pointer": "/remote_stac_url"}],
    }


@unit
def test_publish_stac_rejects_non_project_catalog_id(publishing_http_client):
    """Publication outside the projects namespace must not reach upstream."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": "https://example.test/catalog.json",
                "catalog_id": "users/alice",
            },
        )
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
def test_publish_stac_rejects_path_traversal_in_catalog_id(
    publishing_http_client,
    catalog_id,
):
    """catalog_id builds the upstream parent path, so traversal must not pass."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": "https://example.test/catalog.json",
                "catalog_id": catalog_id,
            },
        )
    )

    assert response.status_code == 422
    # The rejection must happen at the EPT boundary: nothing reached the publisher.
    assert publishing_http_client.calls == []


@unit
@pytest.mark.parametrize(
    "remote_stac_url",
    [
        "/proc/self/environ",
        "/etc/passwd",
        "file:///etc/passwd",
        "../../etc/passwd",
        "ftp://example.test/catalog.json",
        "not-a-url",
    ],
)
def test_publish_stac_rejects_non_http_remote_stac_url(publishing_http_client, remote_stac_url):
    """The worker reads non-http(s) values as local files, so they must not reach it."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": remote_stac_url,
                "catalog_id": "projects/demo",
            },
        )
    )

    assert response.status_code == 422
    # The rejection must happen at the EPT boundary: nothing reached the publisher.
    assert publishing_http_client.calls == []


@unit
def test_publish_stac_forwards_validated_url_as_plain_string(publishing_http_client):
    """Validation happens at parse time; the upstream body still carries a plain string."""
    url = "https://minio.dive.edito.eu/oidc-alice/catalog.json"
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {"remote_stac_url": url, "catalog_id": "projects/demo"},
        )
    )

    assert response.status_code == 202
    sent = publishing_http_client.last_call["json"]["remote_stac_url"]
    assert sent == url
    assert isinstance(sent, str)


@unit
def test_publish_stac_rejects_unsupported_request_fields():
    """The public publish request body rejects undocumented caller fields."""
    response = asyncio.run(
        _post(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": "https://example.test/catalog.json",
                "catalog_id": "projects/demo",
                "unexpected": True,
            },
        )
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "The request body did not match the API schema."
    assert response.json()["errors"] == [{"detail": "Field is not supported.", "pointer": "/unexpected"}]


@unit
def test_publish_stac_maps_upstream_rejection():
    """Upstream rejection returns a service-neutral, sanitized API problem."""
    class RejectingUpstream:
        """Fake upstream service that rejects every request."""

        async def request(self, method: str, url: str, **_kwargs) -> httpx.Response:
            """Return a rejected upstream response containing token-like data."""
            return httpx.Response(
                401,
                json={"detail": "bearer token not valid", "access_token": "leaked"},
                request=httpx.Request(method, url),
            )

    async def rejecting_upstream():
        """Return the rejecting fake through FastAPI dependency injection."""
        return RejectingUpstream()

    app.dependency_overrides[get_http_client] = rejecting_upstream
    try:
        response = asyncio.run(
            _post(
                "/v1/edito/stac/publish",
                {
                    "remote_stac_url": "https://example.test/catalog.json",
                    "catalog_id": "projects/demo",
                },
            )
        )
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/publication-request-rejected",
        "title": "Publication request rejected",
        "status": 401,
        "detail": "The publication request could not be accepted.",
        "instance": "/v1/edito/stac/publish",
        "reason": "publication_request_rejected",
        "upstream_response": {
            "detail": "bearer token not valid",
            "access_token": "***REDACTED***",
        },
    }


@unit
def test_publish_stac_maps_upstream_unavailability():
    """Connection failures return a service-neutral publication problem."""
    class UnreachableUpstream:
        """Fake upstream service that raises a connection error."""

        async def request(self, method: str, url: str, **_kwargs) -> httpx.Response:
            """Raise a connection failure for every outbound request."""
            raise httpx.ConnectError("no route", request=httpx.Request(method, url))

    async def unreachable_upstream():
        """Return the unreachable fake through FastAPI dependency injection."""
        return UnreachableUpstream()

    app.dependency_overrides[get_http_client] = unreachable_upstream
    try:
        response = asyncio.run(
            _post(
                "/v1/edito/stac/publish",
                {
                    "remote_stac_url": "https://example.test/catalog.json",
                    "catalog_id": "projects/demo",
                },
            )
        )
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/publication-backend-unavailable",
        "title": "Publication backend unavailable",
        "status": 502,
        "detail": "EPT could not complete the publication request because a required service is unavailable.",
        "reason": "publication_backend_unavailable",
        "instance": "/v1/edito/stac/publish",
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
    """Return the staging project catalog used by publication tests."""
    return require_live_env("PUBLISH_PROJECT_ID")


@pytest.fixture
def live_stac_source() -> str:
    """Return the remote STAC URL used by publication tests."""
    return require_live_env("PUBLISH_REMOTE_STAC_URL")


@integration
def test_publish_stac_queues_live_publication_job(
    live_stac_source: str,
    live_catalog_id: str,
    live_edito_token_pair,
):
    """Queue a real publication job when integration tests are selected."""
    response = asyncio.run(
        _submit_live(
            "/v1/edito/stac/publish",
            {
                "remote_stac_url": live_stac_source,
                "catalog_id": live_catalog_id,
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
