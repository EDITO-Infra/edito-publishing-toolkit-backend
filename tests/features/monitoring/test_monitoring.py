"""Tests for the monitoring feature routes: buffered logs and process status."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator

import httpx
import pytest

from ept.api.dependencies import require_edito_bearer_auth
from ept.api.main import app
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from ept.infrastructure.utils import monitoring as monitoring_module
from ept.infrastructure.utils.monitoring import get_log_buffer
from tests.conftest import feature_is_active, integration, live_edito_credentials, live_edito_token_pair, unit

# These tests exercise mounted routes, so they skip if the feature is ever
# declared for a later release than EPT_ACTIVE_RELEASE (it ships in 1.0.0).
pytestmark = pytest.mark.skipif(
    not feature_is_active("monitoring.query"),
    reason="feature is not mounted in the active release",
)


def _record(
    msg: str,
    level: int = logging.INFO,
    name: str = "ept.features.monitoring.test",
    created: float = 1000.0,
    **extras: object,
) -> logging.LogRecord:
    record = logging.LogRecord(name, level, "path.py", 1, msg, (), None)
    record.created = created
    for key, value in extras.items():
        setattr(record, key, value)
    return record


@pytest.fixture
def buffered_events(monkeypatch, tmp_path) -> Iterator[None]:
    """Seed an isolated log store with known events and clean up afterwards."""
    monkeypatch.setenv("MONITORING_DB_PATH", str(tmp_path / "monitoring.db"))
    monkeypatch.setattr(monitoring_module, "_buffer", None)
    buffer = get_log_buffer()
    buffer.clear()
    buffer.handle(_record("upload finished", created=1000.0, event="convert", size=42))
    buffer.handle(_record("request failed", logging.ERROR, "ept.api.app", 1005.0, event="http_request"))
    buffer.handle(_record("debug noise", logging.DEBUG, "ept.api.app", 1010.0))
    try:
        yield
    finally:
        buffer.clear()


async def _get(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    """Send an in-process GET request to the EPT application."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.get(path, headers=headers)


@unit
def test_monitoring_logs_returns_events_newest_first(buffered_events):
    """The logs endpoint returns buffered events, newest first."""
    response = asyncio.run(_get("/v1/monitoring/logs?level=DEBUG"))

    assert response.status_code == 200
    body = response.json()
    assert [event["message"] for event in body["events"]] == [
        "debug noise",
        "request failed",
        "upload finished",
    ]
    assert body["events"][0]["level"] == "DEBUG"
    assert body["matched"] == 3
    assert body["returned"] == 3


@unit
def test_monitoring_logs_applies_filters(buffered_events):
    """Level, logger prefix, event tag, text, and limit filters narrow the page."""
    response = asyncio.run(_get("/v1/monitoring/logs?level=WARNING"))
    assert [event["message"] for event in response.json()["events"]] == ["request failed"]

    response = asyncio.run(_get("/v1/monitoring/logs?level=DEBUG&logger=ept.api"))
    assert response.json()["matched"] == 2

    response = asyncio.run(_get("/v1/monitoring/logs?event=http_request"))
    assert [event["message"] for event in response.json()["events"]] == ["request failed"]

    response = asyncio.run(_get("/v1/monitoring/logs?q=upload"))
    assert response.json()["matched"] == 1

    response = asyncio.run(_get("/v1/monitoring/logs?limit=1"))
    body = response.json()
    assert body["returned"] == 1 and body["matched"] == 2


@unit
def test_monitoring_logs_rejects_unknown_query_params(buffered_events):
    """The query contract forbids unknown parameters."""
    response = asyncio.run(_get("/v1/monitoring/logs?bogus=1"))

    assert response.status_code == 422


@unit
def test_monitoring_status_returns_process_snapshot():
    """The status endpoint reports versions, buffer health, and counters."""
    response = asyncio.run(_get("/v1/monitoring/status"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["uptime_seconds"] >= 0
    assert body["buffer"]["capacity"] > 0
    assert set(body["requests"]) == {"active", "total", "server_errors"}


@unit
def test_monitoring_openapi_contract():
    """OpenAPI documents both routes with bearer security."""
    schema = app.openapi()

    logs_operation = schema["paths"]["/v1/monitoring/logs"]["get"]
    assert logs_operation["operationId"] == "getMonitoringLogsV1"
    assert logs_operation["security"] == [{"HTTPBearer": []}]
    status_operation = schema["paths"]["/v1/monitoring/status"]["get"]
    assert status_operation["operationId"] == "getMonitoringStatusV1"
    assert status_operation["security"] == [{"HTTPBearer": []}]


@integration
def test_monitoring_status_live(live_edito_credentials, live_edito_token_pair):
    """The feature route returns live process status for an authenticated user."""

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
        response = asyncio.run(_get("/v1/monitoring/status"))
    finally:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"
