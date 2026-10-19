# Monitoring

Look inside a running EPT API: the most recent log events it produced, and a live snapshot of process health. Use it to answer "what just happened?" — which validation ran, why a conversion failed, how many requests the API is serving — without SSH-ing into the container to read log files.

## Endpoints

```text
GET /v1/monitoring/logs
GET /v1/monitoring/status
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl "${EPT_API_URL}/v1/monitoring/logs?level=INFO&limit=100" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq .
```

```bash
curl "${EPT_API_URL}/v1/monitoring/status" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" | jq .
```

### Python (`httpx`)

```python
import os

import httpx

response = httpx.get(
    f"{os.environ['EPT_API_URL']}/v1/monitoring/logs",
    headers={"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"},
    params={"level": "INFO", "limit": 100},
    timeout=30.0,
)
response.raise_for_status()
events = response.json()["events"]
```

## Querying logs

All parameters are optional; combine them freely:

| Parameter | Default | Meaning |
| --- | --- | --- |
| `level` | `INFO` | Minimum log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `logger` | — | Only loggers whose name starts with this prefix, e.g. `ept.features.validate_stac`. |
| `event` | — | Only events tagged with this name, e.g. `http_request`. |
| `since` | — | Only events at or after this time (ISO 8601). |
| `q` | — | Case-insensitive substring match across the message, logger name, event tag, and structured fields. |
| `limit` | `100` | Maximum events returned (1–100000), newest first. |

Examples:

```bash
# Everything the validate STAC feature logged today at INFO or above
curl "${EPT_API_URL}/v1/monitoring/logs?logger=ept.features.validate_stac&level=INFO" ...

# Free-text search — matches messages, logger names, event tags, and fields
curl "${EPT_API_URL}/v1/monitoring/logs?q=adapter" ...

# Only request-lifecycle logs
curl "${EPT_API_URL}/v1/monitoring/logs?event=http_request" ...
```

## Response (logs)

```json
{
  "events": [
    {
      "ts": "2026-10-14T10:38:46.123456+00:00",
      "level": "INFO",
      "logger": "ept.features.validate_stac.adapter",
      "event": null,
      "message": "VALIDATE OK: https://.../catalog.json [catalog] id='root' depth=0 schema=0.0421s edito=0.0018s issues=0",
      "fields": {"stac_url": "https://..."}
    }
  ],
  "matched": 42,
  "returned": 42,
  "buffer_size": 512,
  "buffer_capacity": 200000,
  "dropped": 0
}
```

- `events` — newest first. `fields` holds the structured extras the emitting code attached; sensitive values (tokens, passwords) are redacted automatically.
- `matched` / `returned` — how many events pass the filters in total, and how many are on this page. Raise `limit` or narrow the filters to see more.
- `buffer_size` / `buffer_capacity` / `dropped` — log-store health. `dropped > 0` means the oldest events were pruned because the retention capacity was reached.

## Response (status)

```json
{
  "status": "ok",
  "version": "1.0.0",
  "python_version": "3.13.7",
  "pid": 7,
  "started_at": "2026-10-14T08:00:00.000000+00:00",
  "uptime_seconds": 9531.4,
  "log_level": "INFO",
  "log_file": "logs/api.log",
  "buffer": {"capacity": 200000, "size": 512, "dropped": 0},
  "requests": {"active": 1, "total": 843, "server_errors": 2}
}
```

## Good to know

- The log store is a local SQLite database written next to the API log (`<API_LOG dir>/monitoring.db`, overridable via `MONITORING_DB_PATH`): it survives restarts, is not shared between workers, and retains the most recent 200000 events (hardcoded in `ept/infrastructure/utils/monitoring.py`). `dropped > 0` on the status endpoint means eviction pressure. If the store can't be created (read-only directory), monitoring falls back to in-memory and the API still starts. The complete unpruned history stays in the rotating log file (`API_LOG`, `logs/api.log` by default).
- Searching with `q` also matches the logger name and event tag — so `q=adapter` finds logs emitted by `ept.features.validate_stac.adapter`, even when the message itself does not contain the word.
- Requests to the monitoring endpoints themselves are **not** logged, so the UI polling them does not flood the buffer with its own traffic.
- Some detail is only logged at `DEBUG` (e.g. per-object validation outcomes); the buffer captures `ept.*` DEBUG records regardless of `LOG_LEVEL`, so query them with `level=DEBUG` without changing process logging.
- A typical workflow: [validate](validate-stac.md) a catalog, then check `/v1/monitoring/logs?logger=ept.features.validate_stac` to see the run's step-by-step log trail.
