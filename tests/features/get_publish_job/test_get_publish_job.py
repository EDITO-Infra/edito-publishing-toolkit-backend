"""Route tests for publication-job summaries and cursor-paginated logs.

The live lookup requires fresh EDITO credentials and ``EDITO_DEMO_JOB_ID``.
It is read-only and needs no explicit mutation opt-in.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ept.api.main import app
from ept.features.get_publish_job.models import DEMO_DETAIL_LOGS, DEMO_JOB_SUMMARY
from tests.conftest import integration, require_live_env, unit

JOB_ID = "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670"


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
def test_default_returns_complete_job_summary(publishing_http_client):
    publishing_http_client.body = _summary()

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}"))

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

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}"))

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

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}?view=detail&limit=50"))

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
        f"/v1/edito/publish/jobs/{JOB_ID}?view=detail&limit=50&cursor=1"
    )
    assert publishing_http_client.last_call["params"] == {"view": "detail", "limit": 50}


@unit
def test_cursor_is_forwarded_as_upstream_after_id(publishing_http_client):
    publishing_http_client.body = _page([_detail_event(2)], limit=25)

    response = asyncio.run(
        _get(f"/v1/edito/publish/jobs/{JOB_ID}?view=detail&limit=25&cursor=1")
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

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}?view=raw"))

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
    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}?{query}"))

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

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}?view=detail"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert "continuation link" in str(response.json()["upstream_response"])


@unit
def test_rate_limit_retains_retry_after(publishing_http_client):
    publishing_http_client.responses = [(429, {"detail": "slow down"}, {"Retry-After": "15"})]

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}"))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "15"


@unit
def test_invalid_upstream_success_shape_maps_to_502(publishing_http_client):
    publishing_http_client.body = {"id": JOB_ID, "status": "finished"}

    response = asyncio.run(_get(f"/v1/edito/publish/jobs/{JOB_ID}"))

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["upstream_response"] == "Publication backend returned an invalid response."


@unit
def test_openapi_documents_query_model_summary_and_pagination():
    schema = app.openapi()
    operation = schema["paths"]["/v1/edito/publish/jobs/{job_id}"]["get"]

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



async def _submit_live(job_id: str, *, access_token: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=60.0,
    ) as client:
        return await client.get(
            f"/v1/edito/publish/jobs/{job_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
