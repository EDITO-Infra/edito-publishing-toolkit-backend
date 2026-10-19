"""Monitoring primitives: structured log store and runtime status.

Log events are captured alongside the console and rotating-file handlers
through the same ``QueueListener`` and persisted to a local SQLite database
(WAL mode) so they survive restarts and never silently evict mid-search. The
store stays process-local by design, which matches the single-worker uvicorn
deployment in ``deploy/docker``; move it to shared storage when EPT runs
multiple replicas.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import os
import platform
import sqlite3
import sys
import threading
import time
from pathlib import Path
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

# Retention bound for the monitoring log store (rows). ~100-200 MB of SQLite
# at ~1 KB/row; honey: raise further only alongside an FTS5 index — past
# ~1M rows full-text ``q`` scans get slow.
LOG_BUFFER_CAPACITY = 200_000

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
    """Persist the most recent log records as structured, JSON-safe events.

    Backed by SQLite (WAL): ``capacity`` is a retention bound enforced by
    pruning the oldest rows, and ``dropped`` counts pruned rows so the status
    endpoint still reports eviction pressure. The same handler works with an
    in-memory database (default, used by tests) or a file path.
    """

    _STD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))
    _RESERVED = frozenset({"message", "asctime", "event"})

    def __init__(self, capacity: int = 10000, path: str = ":memory:") -> None:
        super().__init__()
        self._capacity = max(1, capacity)
        self.dropped = 0
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                created REAL NOT NULL,
                ts TEXT NOT NULL,
                level TEXT NOT NULL,
                level_no INTEGER NOT NULL,
                logger TEXT NOT NULL,
                event TEXT,
                message TEXT NOT NULL,
                fields TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_created ON events(created)"
        )
        self._conn.commit()

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def clear(self) -> None:
        """Discard all stored events (used by tests and log rotation hygiene)."""
        with self._lock:
            self._conn.execute("DELETE FROM events")
            self._conn.commit()
            self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        fields = {
            key: _json_safe(value)
            for key, value in record.__dict__.items()
            if key not in self._STD_ATTRS and key not in self._RESERVED
        }
        event = {
            "created": record.created,
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.__dict__.get("event"),
            "message": record.getMessage(),
            "fields": sanitize_payload(fields),
        }
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (created, ts, level, level_no, logger, event, message, fields)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["created"],
                    event["ts"],
                    event["level"],
                    record.levelno,
                    event["logger"],
                    event["event"],
                    event["message"],
                    json.dumps(event["fields"], default=repr),
                ),
            )
            pruned = self._conn.execute(
                "DELETE FROM events WHERE seq <= (SELECT MAX(seq) FROM events) - ?",
                (self._capacity,),
            )
            self.dropped += pruned.rowcount
            self._conn.commit()

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
        clauses: list[str] = []
        params: list[Any] = []
        if level_no > logging.NOTSET:
            clauses.append("level_no >= ?")
            params.append(level_no)
        if logger_prefix:
            clauses.append("logger LIKE ? || '%'")
            params.append(logger_prefix)
        if event_name:
            clauses.append("event = ?")
            params.append(event_name)
        if since_ts is not None:
            clauses.append("created >= ?")
            params.append(since_ts)
        if contains:
            # Search the logger name, event tag, and structured fields too:
            # feature logs often identify themselves only by logger (e.g.
            # ``...validate_stac.adapter``).
            needle = f"%{contains.lower()}%"
            clauses.append(
                "(instr(lower(message), ?) OR instr(lower(logger), ?)"
                " OR instr(lower(coalesce(event, '')), ?) OR instr(lower(fields), ?))"
            )
            params.extend([contains.lower(), contains.lower(), contains.lower(), contains.lower()])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._lock:
            matched = self._conn.execute(
                f"SELECT COUNT(*) FROM events{where}", params
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT created, ts, level, logger, event, message, fields"
                f" FROM events{where}"
                " ORDER BY created DESC, seq DESC LIMIT ?",
                [*params, limit],
            ).fetchall()
        events = [
            {
                # Internal sort key; stripped by the public LogEvent model.
                "created": row[0],
                "ts": row[1],
                "level": row[2],
                "logger": row[3],
                "event": row[4],
                "message": row[5],
                "fields": json.loads(row[6]),
            }
            for row in rows
        ]
        return events, matched


def get_log_buffer() -> LogBufferHandler:
    """Return the process-wide log buffer, creating it on first use.

    Persisted next to ``API_LOG`` (overridable via ``MONITORING_DB_PATH``)
    with the hardcoded retention bound from ``LOG_BUFFER_CAPACITY``.
    ``dropped`` on the status endpoint reports eviction pressure against that
    bound. If the store can't be created (read-only workdir, bad mount),
    monitoring degrades to in-memory rather than blocking API startup.
    """
    global _buffer
    if _buffer is None:
        db_path = os.getenv("MONITORING_DB_PATH")
        if db_path is None:
            db_path = str(Path(os.getenv("API_LOG", "logs/api.log")).parent / "monitoring.db")
        try:
            _buffer = LogBufferHandler(capacity=LOG_BUFFER_CAPACITY, path=db_path)
        except (OSError, sqlite3.Error) as exc:
            print(
                f"monitoring: log store '{db_path}' unavailable ({exc}); "
                "falling back to in-memory",
                file=sys.stderr,
            )
            _buffer = LogBufferHandler(capacity=LOG_BUFFER_CAPACITY)
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
