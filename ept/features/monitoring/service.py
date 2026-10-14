"""Use-case logic for the monitoring feature."""

from __future__ import annotations

from datetime import UTC, datetime
import logging

from ept.infrastructure.utils.monitoring import (
    LEVEL_ORDER,
    get_log_buffer,
    process_status,
)

from .models import (
    LogEvent,
    MonitoringLogsQuery,
    MonitoringLogsResponse,
    MonitoringStatusResponse,
)

_INTERNAL_KEYS = frozenset({"created"})


def get_monitoring_logs(query: MonitoringLogsQuery) -> MonitoringLogsResponse:
    """Return a newest-first page of buffered log events matching the filters."""
    buffer = get_log_buffer()
    events, matched = buffer.search(
        level_no=LEVEL_ORDER.get(query.level, logging.INFO),
        logger_prefix=query.logger,
        event_name=query.event,
        since_ts=_since_timestamp(query.since),
        contains=query.q,
        limit=query.limit,
    )
    return MonitoringLogsResponse(
        events=[
            LogEvent.model_validate({k: v for k, v in event.items() if k not in _INTERNAL_KEYS})
            for event in events
        ],
        matched=matched,
        returned=len(events),
        buffer_size=len(buffer),
        buffer_capacity=buffer.capacity,
        dropped=buffer.dropped,
    )


def get_monitoring_status() -> MonitoringStatusResponse:
    """Return the live process status snapshot."""
    return MonitoringStatusResponse.model_validate(process_status())


def _since_timestamp(since: datetime | None) -> float | None:
    """Interpret a naive ``since`` filter as UTC and convert to an epoch float."""
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return since.timestamp()
