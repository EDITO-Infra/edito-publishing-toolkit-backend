"""Tests for the SQLite-backed structured log store and process status."""

from __future__ import annotations

import logging

from ept.infrastructure.utils import monitoring as monitoring_module
from ept.infrastructure.utils.monitoring import (
    LogBufferHandler,
    get_log_buffer,
    process_status,
    record_request_end,
    record_request_start,
)

from tests.conftest import unit


def _record(
    msg: str,
    level: int = logging.INFO,
    name: str = "ept.test",
    created: float = 1000.0,
    **extras: object,
) -> logging.LogRecord:
    record = logging.LogRecord(name, level, "path.py", 1, msg, (), None)
    record.created = created
    for key, value in extras.items():
        setattr(record, key, value)
    return record


@unit
def test_buffer_captures_structured_fields_and_event_tags():
    handler = LogBufferHandler(capacity=10)
    handler.handle(_record("did work", operation="zarr", size=42, event="convert"))

    events, matched = handler.search()
    assert matched == 1
    assert events[0]["message"] == "did work"
    assert events[0]["event"] == "convert"
    assert events[0]["fields"] == {"operation": "zarr", "size": 42}


@unit
def test_buffer_redacts_sensitive_extra_values():
    handler = LogBufferHandler(capacity=10)
    handler.handle(_record("auth attempt", password="secret"))

    assert handler.search()[0][0]["fields"]["password"] == "***REDACTED***"


@unit
def test_buffer_search_filters_by_level_logger_and_text():
    handler = LogBufferHandler(capacity=10)
    handler.handle(_record("low detail", logging.DEBUG, "ept.features.a", created=1))
    handler.handle(_record("request done", logging.INFO, "ept.api.app", created=2))
    handler.handle(_record("request failed", logging.ERROR, "ept.api.app", created=3))

    events, matched = handler.search(level_no=logging.WARNING)
    assert matched == 1 and events[0]["message"] == "request failed"

    events, matched = handler.search(logger_prefix="ept.features")
    assert matched == 1 and events[0]["logger"] == "ept.features.a"

    events, matched = handler.search(contains="DONE")
    assert matched == 1 and events[0]["message"] == "request done"

    handler.handle(_record("done", logging.INFO, "ept.features.zarr.adapter", created=1.5, event="convert"))

    events, matched = handler.search(contains="adapter")
    assert matched == 1 and events[0]["logger"] == "ept.features.zarr.adapter"

    events, matched = handler.search(contains="convert")
    assert matched == 1 and events[0]["event"] == "convert"

    events, matched = handler.search(since_ts=2.5)
    assert matched == 1 and events[0]["message"] == "request failed"


@unit
def test_buffer_returns_newest_first_page_and_counts_dropped():
    handler = LogBufferHandler(capacity=2)
    for index in range(5):
        handler.handle(_record(f"event {index}", created=float(index)))

    events, matched = handler.search(limit=1)
    assert matched == 2
    assert [event["message"] for event in events] == ["event 4"]
    assert handler.dropped == 3
    assert len(handler) == 2


@unit
def test_get_log_buffer_is_a_process_wide_singleton():
    assert get_log_buffer() is get_log_buffer()


@unit
def test_process_status_reports_versions_buffer_and_counters(monkeypatch, tmp_path):
    monkeypatch.setenv("MONITORING_DB_PATH", str(tmp_path / "monitoring.db"))
    monkeypatch.setattr(monitoring_module, "_buffer", None)
    before = process_status()["requests"]
    record_request_start()
    record_request_start()
    record_request_end(500)
    try:
        status = process_status()
        requests = status["requests"]
        assert status["status"] == "ok"
        assert status["version"] and status["python_version"]
        assert isinstance(status["pid"], int)
        assert status["uptime_seconds"] >= 0
        assert status["buffer"]["capacity"] > 0
        assert requests["active"] - before["active"] == 1
        assert requests["total"] - before["total"] == 2
        assert requests["server_errors"] - before["server_errors"] == 1
    finally:
        record_request_end(200)


@unit
def test_search_survives_concurrent_emits():
    """Regression: search iterates the deque while emit appends from another
    thread (QueueListener); deque iteration must not raise RuntimeError."""
    import threading

    buffer = LogBufferHandler(capacity=2000)
    stop = threading.Event()

    def emit_loop():
        while not stop.is_set():
            record = logging.LogRecord("ept.test", logging.INFO, __file__, 1, "tick", (), None)
            buffer.emit(record)

    emitter = threading.Thread(target=emit_loop, daemon=True)
    emitter.start()
    try:
        for _ in range(200):
            buffer.search(level_no=logging.INFO, limit=100)
    finally:
        stop.set()
        emitter.join()
