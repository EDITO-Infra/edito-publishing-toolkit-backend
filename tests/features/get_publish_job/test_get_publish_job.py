"""Route tests for publication-job summaries and cursor-paginated logs.

The live lookup requires fresh EDITO credentials and ``EDITO_DEMO_JOB_ID``.
It is read-only and needs no explicit mutation opt-in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ept.api.dependencies import EditoBearerAuth, get_http_client, require_edito_bearer_auth
from ept.api.main import app
from ept.features.get_publish_job.models import DEMO_DETAIL_LOGS, DEMO_JOB_SUMMARY
from ept.features.get_publish_job.transport import parse_publishing_next_cursor
from ept.infrastructure.services.errors import UpstreamServiceRequestError
from tests.conftest import feature_is_active, integration, require_live_env, unit

# These tests exercise mounted routes, so they skip if the feature is ever
# declared for a later release than EPT_ACTIVE_RELEASE (it ships in 1.0.0).
pytestmark = pytest.mark.skipif(
    not feature_is_active("publish.job.get"),
    reason="feature is not mounted in the active release",
)

JOB_ID = "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670"


class RecordingPublishingHttpClient:
    """Record outbound requests and return configured JSON responses.

    Tests use ``body`` for a single response or ``responses`` for an ordered
    sequence. No network request is made.
    """

    def __init__(
        self,
        responses: list[tuple[int, dict[str, Any], dict[str, str]]] | None = None,
        *,
        status_code: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body or {}
        self.responses = list(responses or [])
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


def _summary(job_id: str = JOB_ID) -> dict:
    return {
        "id": job_id,
        "type": "stac_publish",
        "status": "succeeded",
        "username": "alice",
        "created_at": "2026-07-01T10:00:00Z",
        "started_at": "2026-07-01T10:00:01Z",
        "finished_at": "2026-07-01T10:00:03Z",
        "message": "Job completed successfully.",
    }


def _detail_event(event_id: int, *, message: str = "Validated catalog") -> dict:
    return {
        "id": event_id,
        "job_id": JOB_ID,
        "occurred_at": f"2026-07-01T10:00:{event_id:02d}Z",
        "name": "stac.object.validate",
        "status": "success",
        "level": "INFO",
        "message": message,
    }


def _raw_event(event_id: int) -> dict:
    return {
        "id": event_id,
        "occurred_at": f"2026-07-01T10:00:{event_id:02d}Z",
        "name": "stac.object.validate",
        "status": "success",
        "level": "INFO",
        "message": "Validated catalog",
        "payload": {"object_id": f"demo-{event_id}", "unicode": "café"},
    }


def _page(
    items: list[dict],
    *,
    limit: int = 100,
    next_url: str | None = None,
    total: int | None = None,
    message: str = "Returned job events.",
) -> dict:
    return {
        "job": _summary(),
        "events": {
            "items": items,
            "total": len(items) if total is None else total,
            "limit": limit,
            "next": next_url,
            "message": message,
        },
    }


@unit
def test_cursor_parsing_accepts_valid_continuation_links():
    """The documented ``after_id`` query parameter becomes the next cursor."""
    assert parse_publishing_next_cursor(f"https://publishing.test/jobs/{JOB_ID}?view=detail&after_id=42") == 42
    assert parse_publishing_next_cursor(f"/jobs/{JOB_ID}?after_id=0") == 0


@unit
def test_cursor_parsing_rejects_missing_or_invalid_cursors():
    """Missing, non-integer, duplicated, or negative cursors fail closed."""
    with pytest.raises(UpstreamServiceRequestError, match="HTTP 502"):
        parse_publishing_next_cursor(f"/jobs/{JOB_ID}?view=detail")
    with pytest.raises(UpstreamServiceRequestError, match="HTTP 502"):
        parse_publishing_next_cursor(f"/jobs/{JOB_ID}?after_id=abc")
    with pytest.raises(UpstreamServiceRequestError, match="HTTP 502"):
        parse_publishing_next_cursor(f"/jobs/{JOB_ID}?after_id=1&after_id=2")
    with pytest.raises(UpstreamServiceRequestError, match="HTTP 502"):
        parse_publishing_next_cursor(f"/jobs/{JOB_ID}?after_id=-1")


@unit
def test_default_returns_complete_job_summary(publishing_http_client):
    publishing_http_client.body = _summary()

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}"))

    assert response.status_code == 200
    assert response.json() == _summary()
    assert set(response.json()) == {
        "id", "type", "status", "username", "created_at", "started_at", "finished_at", "message"
    }
    assert publishing_http_client.last_call == {
        "method": "GET",
        "url": f"https://publishing.test/jobs/{JOB_ID}",
        "json": None,
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer access-secret",
        },
    }


@unit
def test_default_omits_optional_timestamps_not_returned_by_publisher(publishing_http_client):
    summary = _summary()
    del summary["started_at"]
    del summary["finished_at"]
    publishing_http_client.body = summary

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}"))

    assert response.status_code == 200
    assert response.json() == summary


@unit
def test_detail_view_returns_summary_and_public_next_link(publishing_http_client):
    publishing_http_client.body = _page(
        [_detail_event(1)],
        limit=50,
        next_url=f"/jobs/{JOB_ID}?view=detail&limit=50&after_id=1",
        total=2,
    )

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}?view=detail&limit=50"))

    assert response.status_code == 200
    assert response.json()["id"] == JOB_ID
    assert response.json()["status"] == "succeeded"
    assert response.json()["message"] == "Job completed successfully."
    assert response.json()["page_message"] == "Returned job events."
    assert "job" not in response.json()
    assert response.json()["logs"][0]["message"] == "Validated catalog"
    assert response.json()["total"] == 2
    assert response.json()["limit"] == 50
    assert response.json()["next"] == (
        f"/v1/edito/stac/jobs/{JOB_ID}?view=detail&limit=50&cursor=1"
    )
    assert publishing_http_client.last_call["params"] == {"view": "detail", "limit": 50}


@unit
def test_cursor_is_forwarded_as_upstream_after_id(publishing_http_client):
    publishing_http_client.body = _page([_detail_event(2)], limit=25)

    response = asyncio.run(
        _get(f"/v1/edito/stac/jobs/{JOB_ID}?view=detail&limit=25&cursor=1")
    )

    assert response.status_code == 200
    assert response.json()["next"] is None
    assert publishing_http_client.last_call["params"] == {
        "view": "detail",
        "limit": 25,
        "after_id": 1,
    }


@unit
def test_raw_view_returns_canonical_logs_and_null_next(publishing_http_client):
    publishing_http_client.body = _page([_raw_event(1)])

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}?view=raw"))

    assert response.status_code == 200
    assert response.json()["logs"][0]["payload"]["object_id"] == "demo-1"
    assert response.json()["logs"][0]["message"] == "Validated catalog"
    assert response.json()["page_message"] == "Returned job events."
    assert response.json()["next"] is None
    assert publishing_http_client.last_call["params"] == {"view": "raw", "limit": 100}


@unit
@pytest.mark.parametrize(
    "query",
    [
        "view=unknown",
        "view=detail&limit=0",
        "view=detail&limit=1001",
        "cursor=1",
        "detail=true",
        "export=true",
    ],
)
def test_invalid_or_legacy_query_parameters_return_422(query, publishing_http_client):
    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}?{query}"))

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"].endswith("/invalid-request")
    assert publishing_http_client.calls == []


@unit
def test_invalid_upstream_continuation_link_maps_to_502(publishing_http_client):
    publishing_http_client.body = _page(
        [_detail_event(1)],
        next_url=f"/jobs/{JOB_ID}?view=detail",
    )

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}?view=detail"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert "continuation link" in str(response.json()["upstream_response"])


@unit
def test_rate_limit_retains_retry_after(publishing_http_client):
    publishing_http_client.responses = [(429, {"detail": "slow down"}, {"Retry-After": "15"})]

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}"))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "15"


@unit
def test_invalid_upstream_success_shape_maps_to_502(publishing_http_client):
    publishing_http_client.body = {"id": JOB_ID, "status": "finished"}

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["upstream_response"] == "Publication backend returned an invalid response."


@unit
def test_openapi_documents_query_model_summary_and_pagination():
    schema = app.openapi()
    operation = schema["paths"]["/v1/edito/stac/jobs/{job_id}"]["get"]

    assert operation["security"] == [{"HTTPBearer": []}]
    bearer_scheme = schema["components"]["securitySchemes"]["HTTPBearer"]
    assert bearer_scheme["scheme"] == "bearer"
    assert bearer_scheme["bearerFormat"] == "JWT"
    assert operation["operationId"] == "getPublicationJobV1"
    assert "requestBody" not in operation
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "job_id",
        "view",
        "limit",
        "cursor",
    }

    view = _parameter(operation, "view")
    assert view["in"] == "query"
    assert view["required"] is False
    assert view["schema"]["default"] == "summary"
    assert view["schema"]["enum"] == ["summary", "detail", "raw"]

    limit = _parameter(operation, "limit")
    assert limit["schema"]["default"] == 100
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 1000

    cursor = _parameter(operation, "cursor")
    assert cursor["required"] is False
    assert _schema_has_integer(cursor["schema"])

    response = operation["responses"]["200"]
    assert set(response["content"]) == {"application/json"}
    assert "Content-Disposition" not in response.get("headers", {})
    examples = response["content"]["application/json"]["examples"]
    assert set(examples) == {"summary", "detail"}
    assert examples["summary"]["value"] == {
        key: value for key, value in DEMO_JOB_SUMMARY.items() if value is not None
    }
    assert examples["detail"]["value"] == {
        key: value for key, value in DEMO_DETAIL_LOGS.items() if value is not None
    }


@unit
def test_lists_jobs_scoped_to_the_token_user(publishing_http_client):
    """GET /jobs forwards the bearer token to the publisher's /jobs."""
    publishing_http_client.body = {"jobs": [_summary()]}

    response = asyncio.run(_get("/v1/edito/stac/jobs"))

    assert response.status_code == 200
    jobs = response.json()["jobs"]
    assert [job["id"] for job in jobs] == [JOB_ID]
    assert set(jobs[0]) == {
        "id", "type", "status", "username", "created_at", "started_at", "finished_at", "message"
    }
    assert publishing_http_client.last_call == {
        "method": "GET",
        "url": "https://publishing.test/jobs",
        "json": None,
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer access-secret",
        },
    }


@unit
def test_empty_job_list_round_trips(publishing_http_client):
    publishing_http_client.body = {"jobs": []}

    response = asyncio.run(_get("/v1/edito/stac/jobs"))

    assert response.status_code == 200
    assert response.json() == {"jobs": []}


@unit
def test_list_limit_caps_returned_jobs(publishing_http_client):
    publishing_http_client.body = {
        "jobs": [_summary("job-1"), _summary("job-2"), _summary("job-3")]
    }

    response = asyncio.run(_get("/v1/edito/stac/jobs?limit=2"))

    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == ["job-1", "job-2"]


@unit
@pytest.mark.parametrize("query", ["limit=0", "limit=1001", "limit=abc"])
def test_list_out_of_range_limit_returns_422(query, publishing_http_client):
    response = asyncio.run(_get(f"/v1/edito/stac/jobs?{query}"))

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert publishing_http_client.calls == []


@unit
def test_list_omits_optional_timestamps_not_returned_by_publisher(publishing_http_client):
    summary = _summary()
    del summary["started_at"]
    del summary["finished_at"]
    publishing_http_client.body = {"jobs": [summary]}

    response = asyncio.run(_get("/v1/edito/stac/jobs"))

    assert response.status_code == 200
    assert "started_at" not in response.json()["jobs"][0]
    assert "finished_at" not in response.json()["jobs"][0]


@unit
def test_list_token_without_username_claim_is_rejected(publishing_http_client):
    async def principal_without_username() -> EditoBearerAuth:
        return EditoBearerAuth(
            subject="test-user",
            username=None,
            claims={"sub": "test-user"},
            access_token="access-secret",
        )

    app.dependency_overrides[require_edito_bearer_auth] = principal_without_username
    try:
        response = asyncio.run(_get("/v1/edito/stac/jobs"))
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 401
    assert response.json()["reason"] == "invalid_bearer_token"
    assert publishing_http_client.calls == []


@unit
def test_list_upstream_rejection_maps_to_sanitized_problem(publishing_http_client):
    publishing_http_client.responses = [
        (401, {"detail": "bearer token not valid", "access_token": "leaked"}, {})
    ]

    response = asyncio.run(_get("/v1/edito/stac/jobs"))

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert "leaked" not in response.text


@unit
def test_list_unreachable_upstream_maps_to_502(publishing_http_client):
    class UnreachableUpstream:
        async def request(self, method: str, url: str, **_kwargs) -> httpx.Response:
            raise httpx.ConnectError("no route", request=httpx.Request(method, url))

    async def unreachable_upstream() -> UnreachableUpstream:
        return UnreachableUpstream()

    app.dependency_overrides[get_http_client] = unreachable_upstream
    try:
        response = asyncio.run(_get("/v1/edito/stac/jobs"))
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"


@unit
def test_list_invalid_upstream_success_shape_maps_to_502(publishing_http_client):
    publishing_http_client.body = {"jobs": [{"id": JOB_ID, "status": "finished"}]}

    response = asyncio.run(_get("/v1/edito/stac/jobs"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"


@unit
def test_uuid_job_ids_reach_the_single_job_route(publishing_http_client):
    """uuid segments keep polling GET /jobs/{job_id} instead of the list route."""
    publishing_http_client.body = _summary()

    response = asyncio.run(_get(f"/v1/edito/stac/jobs/{JOB_ID}"))

    assert response.status_code == 200
    assert publishing_http_client.last_call["url"] == f"https://publishing.test/jobs/{JOB_ID}"


@unit
def test_openapi_documents_list_route_and_job_route():
    schema = app.openapi()
    list_operation = schema["paths"]["/v1/edito/stac/jobs"]["get"]

    assert list_operation["operationId"] == "listPublishJobsV1"
    assert list_operation["security"] == [{"HTTPBearer": []}]
    assert [parameter["name"] for parameter in list_operation["parameters"]] == ["limit"]
    limit = _parameter(list_operation, "limit")
    assert limit["schema"]["default"] == 100
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 1000

    # The uuid convertor must not leak into the documented path of the poller.
    assert "/v1/edito/stac/jobs/{job_id}" in schema["paths"]
    assert {parameter["name"] for parameter in schema["paths"]["/v1/edito/stac/jobs/{job_id}"]["get"]["parameters"]} == {
        "job_id", "view", "limit", "cursor",
    }


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.get(path)


def _parameter(operation: dict, name: str) -> dict:
    return next(parameter for parameter in operation["parameters"] if parameter["name"] == name)


def _schema_has_integer(schema: dict) -> bool:
    return schema.get("type") == "integer" or any(child.get("type") == "integer" for child in schema.get("anyOf", []))


@integration
def test_get_publish_job_reads_live_job_when_job_id_is_supplied(
    live_edito_token_pair,
):
    job_id = require_live_env("EDITO_DEMO_JOB_ID")
    response = asyncio.run(
        _submit_live(job_id, access_token=live_edito_token_pair.access_token)
    )
    assert response.status_code == 200
    assert response.json()["id"] == job_id
    assert "status" in response.json()


@integration
def test_list_publish_jobs_reads_live_jobs(
    live_edito_token_pair,
):
    response = asyncio.run(
        _list_live(access_token=live_edito_token_pair.access_token)
    )
    assert response.status_code == 200, response.text
    jobs = response.json()["jobs"]
    assert isinstance(jobs, list)
    assert all("id" in job and "status" in job for job in jobs)



async def _submit_live(job_id: str, *, access_token: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=60.0,
    ) as client:
        return await client.get(
            f"/v1/edito/stac/jobs/{job_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )


async def _list_live(*, access_token: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=60.0,
    ) as client:
        return await client.get(
            "/v1/edito/stac/jobs",
            headers={"Authorization": f"Bearer {access_token}"},
        )
