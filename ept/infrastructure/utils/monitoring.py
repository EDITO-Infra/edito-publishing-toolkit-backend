"""In-process monitoring primitives: structured log buffer and runtime status.

The buffer is an in-memory ring of structured log events captured alongside the
console and rotating-file handlers through the same ``QueueListener``. It is
intentionally process-local: entries are lost on restart and not shared across
workers, which matches the single-worker uvicorn deployment in ``deploy/docker``.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import logging
import os
import platform
import threading
import time
from typing import Any

from ept import __version__
from ept.infrastructure.utils.logging import sanitize_payload


LEVEL_ORDER: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_START_TIME = time.time()

_counters: dict[str, int] = {"active": 0, "total": 0, "server_errors": 0}
_counters_lock = threading.Lock()

_buffer: LogBufferHandler | None = None


def _json_safe(value: Any) -> Any:
    """Coerce a log extra into a JSON-serializable value without losing detail."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return repr(value)


class LogBufferHandler(logging.Handler):
    """Retain the most recent log records as structured, JSON-safe events."""

    _STD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    _RESERVED = frozenset({"message", "asctime", "event"})

    def __init__(self, capacity: int = 100000) -> None:
        super().__init__()
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.dropped = 0

    @property
    def capacity(self) -> int:
        return self._events.maxlen or 0

    def __len__(self) -> int:
        return len(self._events)

    def clear(self) -> None:
        """Discard all buffered events (used by tests and log rotation hygiene)."""
        with self._lock:
            self._events.clear()
            self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        # deque.append is atomic under the GIL; a full deque silently evicts
        # the oldest entry, which we count for the status endpoint.
        fields = {
            key: _json_safe(value)
            for key, value in record.__dict__.items()
            if key not in self._STD_ATTRS and key not in self._RESERVED
        }
        # The QueueListener thread emits while request threads search the same
        # deque; iteration over a deque is not atomic, so both paths take the
        # lock instead of relying on the GIL.
        with self._lock:
            if len(self._events) == self.capacity:
                self.dropped += 1
            self._events.append(
                {
                    # Internal sort key; stripped by the public LogEvent model.
                    "created": record.created,
                    "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                    "level": record.levelname,
                    "logger": record.name,
                    "event": record.__dict__.get("event"),
                    "message": record.getMessage(),
                    "fields": sanitize_payload(fields),
                }
            )

    def search(
        self,
        *,
        level_no: int = logging.NOTSET,
        logger_prefix: str | None = None,
        event_name: str | None = None,
        since_ts: float | None = None,
        contains: str | None = None,
        limit: int = 1000,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return the newest-first page of matching events plus the match count."""
        with self._lock:
            snapshot = list(self._events)
        matches = [
            event
            for event in snapshot
            if self._matches(event, level_no, logger_prefix, event_name, since_ts, contains)
        ]
        matched = len(matches)
        if limit < matched:
            matches = matches[-limit:]
        return list(reversed(matches)), matched

    @staticmethod
    def _matches(
        event: dict[str, Any],
        level_no: int,
        logger_prefix: str | None,
        event_name: str | None,
        since_ts: float | None,
        contains: str | None,
    ) -> bool:
        if LEVEL_ORDER.get(event["level"], logging.INFO) < level_no:
            return False
        if logger_prefix and not event["logger"].startswith(logger_prefix):
            return False
        if event_name and event["event"] != event_name:
            return False
        if since_ts is not None and event["created"] < since_ts:
            return False
        if contains:
            # Search the logger name and event tag too: feature logs often
            # identify themselves only by logger (e.g. ``...validate_stac.adapter``).
            haystack = " ".join(
                (
                    event["message"],
                    event["logger"],
                    event["event"] or "",
                    str(event["fields"]),
                )
            ).lower()
            if contains.lower() not in haystack:
                return False
        return True


def get_log_buffer() -> LogBufferHandler:
    """Return the process-wide log buffer, creating it on first use."""
    global _buffer
    if _buffer is None:
        _buffer = LogBufferHandler()
    return _buffer


def record_request_start() -> None:
    """Count one in-flight API request."""
    with _counters_lock:
        _counters["active"] += 1
        _counters["total"] += 1


def record_request_end(status_code: int) -> None:
    """Close one API request and count server-side failures."""
    with _counters_lock:
        _counters["active"] -= 1
        if status_code >= 500:
            _counters["server_errors"] += 1


def process_status() -> dict[str, Any]:
    """Return a JSON-safe snapshot of process health for the status endpoint."""
    buffer = get_log_buffer()
    with _counters_lock:
        requests = dict(_counters)
    return {
        "status": "ok",
        "version": __version__,
        "python_version": platform.python_version(),
        "pid": os.getpid(),
        "started_at": datetime.fromtimestamp(_START_TIME, tz=UTC).isoformat(),
        "uptime_seconds": round(time.time() - _START_TIME, 3),
        "log_level": logging.getLevelName(logging.getLogger().getEffectiveLevel()),
        "log_file": os.getenv("API_LOG", "logs/api.log"),
        "buffer": {
            "capacity": buffer.capacity,
            "size": len(buffer),
            "dropped": buffer.dropped,
        },
        "requests": requests,
    }
