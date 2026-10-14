"""Monitoring API routes: buffered log events and process status."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import (
    MonitoringLogsQuery,
    MonitoringLogsResponse,
    MonitoringStatusResponse,
)
from .service import get_monitoring_logs, get_monitoring_status

router = APIRouter(prefix="/v1/monitoring", tags=["Monitoring"])

MONITORING_LOGS_DESCRIPTION = """
Requires `Authorization: Bearer <access_token>`. Returns the most recent
structured log events held in the API process's in-memory ring buffer, newest
first. Filter by minimum `level`, `logger` name prefix, `event` tag, `since`
time, and free-text `q`. The buffer is process-local: it resets on restart and
is not shared across workers; the persistent record remains in the rotating log
file (`API_LOG`).
"""

MONITORING_STATUS_DESCRIPTION = """
Requires `Authorization: Bearer <access_token>`. Returns live process status:
versions, uptime, effective log level, log buffer health, and API request
counters since process start.
"""


@router.get(
    "/logs",
    response_model=MonitoringLogsResponse,
    summary="Query buffered API logs",
    operation_id="getMonitoringLogsV1",
    description=MONITORING_LOGS_DESCRIPTION,
    responses=problem_responses(upstream_backed=False),
)
async def get_monitoring_logs_route(
    _edito_auth: EditoBearerAuthDep,
    query: Annotated[MonitoringLogsQuery, Query()],
) -> MonitoringLogsResponse:
    """Validate the query contract and return one page of buffered log events."""
    return get_monitoring_logs(query)


@router.get(
    "/status",
    response_model=MonitoringStatusResponse,
    summary="Get process monitoring status",
    operation_id="getMonitoringStatusV1",
    description=MONITORING_STATUS_DESCRIPTION,
    responses=problem_responses(upstream_backed=False),
)
async def get_monitoring_status_route(
    _edito_auth: EditoBearerAuthDep,
) -> MonitoringStatusResponse:
    """Return the live process status snapshot."""
    return get_monitoring_status()
