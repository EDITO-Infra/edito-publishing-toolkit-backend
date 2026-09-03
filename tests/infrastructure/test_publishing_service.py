"""Tests for the shared EDITO publishing-service gateway and source contract."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from ept.infrastructure.services.publishing import (
    PublishingServiceClient,
    PublishingServicePublishStacJobRequest,
    PublishingServiceJobAuth,
    PublishingServiceJobDetailResponse,
    PublishingServiceJobSummary,
    PublishingServiceRequestError,
    resolve_publishing_api_url,
    resolve_publishing_openapi_url,
)
from tests.conftest import integration, require_live_env, unit
from tests.infrastructure.publishing_fakes import RecordingPublishingHttpClient

JOB_ID = "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670"


def _summary() -> dict:
    return {
        "id": JOB_ID,
        "type": "stac_publish",
        "status": "succeeded",
        "username": "alice",
        "created_at": "2026-07-01T10:00:00Z",
        "message": "Job completed successfully.",
    }


def _detail_event(event_id: int) -> dict:
    return {
        "id": event_id,
        "job_id": JOB_ID,
        "occurred_at": f"2026-07-01T10:00:{event_id:02d}Z",
        "name": "stac.object.publish",
        "status": "success",
        "level": "INFO",
        "message": "Published STAC object.",
    }


def _detail_page(items: list[dict], next_url: str | None, total: int = 1) -> dict:
    return {
        "job": _summary(),
        "events": {
            "items": items,
            "total": total,
            "limit": 1000,
            "next": next_url,
            "message": "Returned job events.",
        },
    }


class RejectingClient:
    async def request(self, method: str, url: str, **_kwargs) -> httpx.Response:
        return httpx.Response(
            401,
            json={"access_token": "leaked-access", "refresh_token": "leaked-refresh"},
            request=httpx.Request(method, url),
        )


@unit
def test_publish_stac_job_uses_new_path_and_transport_defaults():
    client = RecordingPublishingHttpClient(body={"job_id": JOB_ID, "status": "queued"})
    request = PublishingServicePublishStacJobRequest(
        remote_stac_url="https://example.test/catalog.json",
        parent_path="/catalogs/projects/demo",
    )

    response = asyncio.run(
        PublishingServiceClient(client, publishing_api_url="https://publishing.test/").publish_stac_job(
            request,
            auth=PublishingServiceJobAuth("access-secret", "refresh-secret"),
        )
    )

    assert response.job_id == JOB_ID
    assert response.status == "queued"
    assert client.calls == [
        {
            "method": "POST",
            "url": "https://publishing.test/stac/publish",
            "json": {
                "remote_stac_url": "https://example.test/catalog.json",
                "parent_path": "/catalogs/projects/demo",
                "dry_run": False,
                "overwrite": True,
            },
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer access-secret",
                "X-EDITO-Refresh-Token": "refresh-secret",
            },
        }
    ]


@unit
def test_get_job_summary_uses_bearer_without_upstream_pagination_parameters():
    client = RecordingPublishingHttpClient([(200, _summary(), {})])

    response = asyncio.run(
        PublishingServiceClient(client, publishing_api_url="https://publishing.test").get_job(
            JOB_ID,
            access_token="access-secret",
        )
    )

    assert isinstance(response, PublishingServiceJobSummary)
    assert response.id == JOB_ID
    assert client.calls[0] == {
        "method": "GET",
        "url": f"https://publishing.test/jobs/{JOB_ID}",
        "json": None,
        "headers": {"Accept": "application/json", "Authorization": "Bearer access-secret"},
    }


@unit
def test_get_job_detail_maps_view_limit_and_cursor():
    client = RecordingPublishingHttpClient([(200, _detail_page([_detail_event(2)], None), {})])

    response = asyncio.run(
        PublishingServiceClient(client, publishing_api_url="https://publishing.test").get_job(
            JOB_ID,
            access_token="access-secret",
            view="detail",
            limit=100,
            after_id=1,
        )
    )

    assert isinstance(response, PublishingServiceJobDetailResponse)
    assert response.events.items[0].id == 2
    assert client.calls[0]["params"] == {"view": "detail", "limit": 100, "after_id": 1}


@unit
def test_event_iteration_handles_empty_pages_and_deduplicates_overlap():
    client = RecordingPublishingHttpClient(
        [
            (200, _detail_page([_detail_event(1)], f"/jobs/{JOB_ID}?view=detail&after_id=1", total=2), {}),
            (200, _detail_page([], f"/jobs/{JOB_ID}?view=detail&after_id=2", total=2), {}),
            (200, _detail_page([_detail_event(1), _detail_event(3)], None, total=2), {}),
        ]
    )
    gateway = PublishingServiceClient(client, publishing_api_url="https://publishing.test")

    events = asyncio.run(_collect_events(gateway))

    assert [event["id"] for event in events] == [1, 3]
    assert [call["params"].get("after_id") for call in client.calls] == [None, 1, 2]


@unit
def test_event_iteration_rejects_repeated_cursor():
    repeated = f"/jobs/{JOB_ID}?view=detail&after_id=1"
    client = RecordingPublishingHttpClient(
        [
            (200, _detail_page([_detail_event(1)], repeated), {}),
            (200, _detail_page([_detail_event(2)], repeated), {}),
        ]
    )
    gateway = PublishingServiceClient(client, publishing_api_url="https://publishing.test")

    with pytest.raises(PublishingServiceRequestError, match="HTTP 502") as caught:
        asyncio.run(_collect_events(gateway))
    assert "pagination cursor" in str(caught.value.detail)


@unit
def test_publishing_service_error_redacts_tokens(caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(PublishingServiceRequestError) as caught:
        asyncio.run(
            PublishingServiceClient(RejectingClient(), publishing_api_url="https://publishing.test").publish_stac_job(
                PublishingServicePublishStacJobRequest(
                    remote_stac_url="https://example.test/catalog.json",
                    parent_path="/catalogs/demo",
                ),
                auth=PublishingServiceJobAuth("access-secret", "refresh-secret"),
            )
        )
    assert caught.value.detail == {"access_token": "***REDACTED***", "refresh_token": "***REDACTED***"}
    assert "leaked-access" not in caplog.text
    assert "leaked-refresh" not in caplog.text


@unit
def test_publishing_api_url_is_resolved_from_environment(monkeypatch):
    monkeypatch.setenv("PUBLISHING_SERVICE_API_URL", "https://secondary.test")
    monkeypatch.setenv("PUBLISHING_API_URL", '"https://publishing.test/"')
    assert resolve_publishing_api_url() == "https://publishing.test"
    assert resolve_publishing_openapi_url() == "https://publishing.test/openapi.json"


async def _collect_events(client: PublishingServiceClient) -> list[dict]:
    return [event async for event in client.iter_job_events(JOB_ID, access_token="access-secret", view="detail")]


def _resolve(schema: dict, document: dict) -> dict:
    ref = schema.get("$ref")
    if not ref:
        return schema
    value: object = document
    for part in ref.removeprefix("#/").split("/"):
        assert isinstance(value, dict)
        value = value[part]
    assert isinstance(value, dict)
    return value


@integration
def test_live_publishing_openapi_exposes_ept_contract(live_test_environment):
    """Require the configured publishing service to expose the API EPT uses."""
    publishing_api_url = resolve_publishing_api_url()
    openapi_url = resolve_publishing_openapi_url()
    assert openapi_url == f"{publishing_api_url}/openapi.json"

    response = httpx.get(openapi_url, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    contract = response.json()

    operations = {
        operation["operationId"]: (path, method, operation)
        for path, path_item in contract["paths"].items()
        for method, operation in path_item.items()
        if isinstance(operation, dict) and "operationId" in operation
    }

    publish_path, publish_method, publish = operations["publish_stac_job_stac_publish_post"]
    assert (publish_path, publish_method) == ("/stac/publish", "post")
    publish_schema = _resolve(
        publish["requestBody"]["content"]["application/json"]["schema"],
        contract,
    )
    assert set(publish_schema["required"]) == {"remote_stac_url", "parent_path"}
    assert publish_schema["properties"]["dry_run"]["default"] is False
    assert publish_schema["properties"]["overwrite"]["default"] is True
    assert publish["security"] == [{"BearerAuth": []}]

    delete_path, delete_method, delete = operations["delete_stac_job_stac_delete_post"]
    assert (delete_path, delete_method) == ("/stac/delete", "post")
    assert delete["security"] == [{"BearerAuth": []}]

    job_path, job_method, get_job = operations["get_job_jobs__job_id__get"]
    assert (job_path, job_method) == ("/jobs/{job_id}", "get")
    parameters = {parameter["name"]: parameter for parameter in get_job["parameters"]}
    assert parameters["view"]["schema"]["enum"] == ["summary", "detail", "raw"]
    assert parameters["view"]["schema"]["default"] == "summary"
    assert parameters["limit"]["schema"]["default"] == 1000
    assert parameters["limit"]["schema"]["maximum"] == 1000
    assert parameters["limit"]["schema"]["minimum"] == 1
    assert parameters["after_id"]["schema"]["anyOf"][0]["minimum"] == 0
    assert get_job["security"] == [{"BearerAuth": []}]

    events_path, events_method, events = operations["get_job_events_jobs__job_id__events_get"]
    assert (events_path, events_method) == ("/jobs/{job_id}/events", "get")
    assert {parameter["name"] for parameter in events["parameters"]}.issuperset(
        {"view", "limit", "after_id"}
    )
    assert events["security"] == [{"BearerAuth": []}]

    schemas = contract["components"]["schemas"]
    assert set(schemas["JobSummaryView"]["required"]) == {
        "id",
        "type",
        "status",
        "username",
        "created_at",
        "message",
    }
    assert set(schemas["PaginatedDetailEvents"]["required"]) == {
        "items",
        "total",
        "limit",
        "next",
        "message",
    }
    assert set(schemas["PaginatedRawEvents"]["required"]) == {
        "items",
        "total",
        "limit",
        "next",
        "message",
    }
    assert "message" in schemas["JobEventDetailView"]["required"]
    assert "message" in schemas["JobRawEventView"]["required"]


@integration
def test_live_publish_stac_job_can_queue_dry_run_job(live_edito_token_pair):
    """Test that a STAC job can be queued via the live publishing API. This is a 'dry-run' job to test that job submission works."""
    publishing_api_url = resolve_publishing_api_url()
    remote_stac_url = require_live_env("PUBLISH_REMOTE_STAC_URL")
    project_id = require_live_env("PUBLISH_PROJECT_ID")
    parent_path = f"/catalogs/projects/{project_id}"

    async def submit() -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await PublishingServiceClient(
                client,
                publishing_api_url=publishing_api_url,
            ).publish_stac_job(
                PublishingServicePublishStacJobRequest(
                    remote_stac_url=remote_stac_url,
                    parent_path=parent_path,
                    dry_run=True,
                    overwrite=False,
                ),
                auth=PublishingServiceJobAuth(
                    live_edito_token_pair.access_token,
                    live_edito_token_pair.refresh_token,
                ),
            )
            return response.model_dump()

    response = asyncio.run(submit())

    assert response["job_id"]
    assert response["status"] == "queued"
