"""Public models for the monitoring feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEvent(BaseModel):
    """One structured log event from the in-memory ring buffer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    ts: datetime = Field(description="Event time in UTC (ISO 8601).")
    level: LogLevelName = Field(description="Log level name.")
    logger: str = Field(description="Logger name, typically the emitting module path.")
    event: str | None = Field(
        default=None,
        description="Semantic event tag (e.g. http_request) declared by the call site.",
    )
    message: str = Field(description="Rendered log message.")
    fields: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured extra fields passed by the call site; sensitive values are redacted.",
    )


class MonitoringLogsQuery(BaseModel):
    """Filters for querying the in-memory log buffer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    level: LogLevelName = Field(
        default="INFO",
        description="Minimum log level to include.",
    )
    logger: str | None = Field(
        default=None,
        description="Return only loggers whose name starts with this prefix (e.g. ept.features).",
    )
    event: str | None = Field(
        default=None,
        description="Return only events tagged with this event name (e.g. http_request).",
    )
    since: datetime | None = Field(
        default=None,
        description="Return only events at or after this time (ISO 8601; naive values are treated as UTC).",
    )
    q: str | None = Field(
        default=None,
        description="Case-insensitive substring match across the message, logger name, event tag, and structured fields.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=100000,
        description="Maximum events returned, newest first.",
    )


class LogBufferInfo(BaseModel):
    """Health of the in-memory log ring buffer."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    capacity: int = Field(description="Maximum events the buffer retains.")
    size: int = Field(description="Events currently held.")
    dropped: int = Field(description="Oldest events evicted because the buffer was full.")


class RequestCounters(BaseModel):
    """API request counters since process start."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    active: int = Field(description="Requests currently in flight.")
    total: int = Field(description="Requests received since process start.")
    server_errors: int = Field(description="Requests ended with a 5xx status or exception.")


class MonitoringLogsResponse(BaseModel):
    """One page of buffered log events plus buffer health."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    events: list[LogEvent] = Field(description="Matching events, newest first.")
    matched: int = Field(description="Total buffered events matching the filters.")
    returned: int = Field(description="Number of events in this page.")
    buffer_size: int = Field(description="Events currently held in the ring buffer.")
    buffer_capacity: int = Field(description="Maximum events the ring buffer retains.")
    dropped: int = Field(description="Events evicted from the buffer because it was full.")


class MonitoringStatusResponse(BaseModel):
    """Live process status for monitoring dashboards."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    status: str = Field(description="Process health indicator.")
    version: str = Field(description="EPT API version.")
    python_version: str = Field(description="Python interpreter version.")
    pid: int = Field(description="API process identifier.")
    started_at: datetime = Field(description="Process start time in UTC (ISO 8601).")
    uptime_seconds: float = Field(description="Seconds since process start.")
    log_level: str = Field(description="Effective root logger level.")
    log_file: str = Field(description="Rotating log file path.")
    buffer: LogBufferInfo = Field(description="In-memory log buffer health.")
    requests: RequestCounters = Field(description="API request counters since process start.")


__all__ = [
    "LogBufferInfo",
    "LogEvent",
    "LogLevelName",
    "MonitoringLogsQuery",
    "MonitoringLogsResponse",
    "MonitoringStatusResponse",
    "RequestCounters",
]
