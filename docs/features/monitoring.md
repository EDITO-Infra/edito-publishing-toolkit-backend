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
| `limit` | `100` | Maximum events returned (1–1000), newest first. |

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
  "buffer_capacity": 1000,
  "dropped": 0
}
```

- `events` — newest first. `fields` holds the structured extras the emitting code attached; sensitive values (tokens, passwords) are redacted automatically.
- `matched` / `returned` — how many events pass the filters in total, and how many are on this page. Raise `limit` or narrow the filters to see more.
- `buffer_size` / `buffer_capacity` / `dropped` — ring-buffer health. `dropped > 0` means the oldest events were evicted because the buffer filled up.

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
  "buffer": {"capacity": 1000, "size": 512, "dropped": 0},
  "requests": {"active": 1, "total": 843, "server_errors": 2}
}
```

## Good to know

- The log buffer lives **in the API process memory**: it resets on restart, is not shared between workers, and only holds the most recent ~1000 events. The complete history stays in the rotating log file (`API_LOG`, `logs/api.log` by default).
- Searching with `q` also matches the logger name and event tag — so `q=adapter` finds logs emitted by `ept.features.validate_stac.adapter`, even when the message itself does not contain the word.
- Requests to the monitoring endpoints themselves are **not** logged, so the UI polling them does not flood the buffer with its own traffic.
- Some detail is only logged at `DEBUG` (e.g. schema-level validation detail); at the default `LOG_LEVEL=INFO` those events never reach the buffer. Set `LOG_LEVEL=DEBUG` to capture them.
- A typical workflow: [validate](validate-stac.md) a catalog, then check `/v1/monitoring/logs?logger=ept.features.validate_stac` to see the run's step-by-step log trail.
