# EPT Job Runner

Run long-running feature operations in the background. Instead of holding an HTTP request open (and risking gateway timeouts), you submit a *run*, receive a `run_id` immediately, and poll for the result. Runs can also be cancelled while queued or running.

## Not the same as publication jobs

EPT has two background mechanisms with different vocabularies:

| | EPT job runner (this page) | Publication jobs |
| --- | --- | --- |
| Executes | Inside the EPT API process | On the external EDITO publishing service |
| Identifier | `run_id`, prefixed `run-` | `job_id` (publisher UUID) |
| Endpoints | `GET/POST/DELETE /v1/runs`, `GET /v1/runs/operations` | `GET /v1/edito/publish/jobs/{job_id}` |
| Used by | Feature operations such as `stac.validate` | [Publish STAC](publish-stac.md) and [Remove STAC](remove-stac.md) |

## Endpoints

```text
POST   /v1/runs              submit a run (202)
GET    /v1/runs              list your runs (newest first)
GET    /v1/runs/{run_id}     poll status and result
DELETE /v1/runs/{run_id}     cancel a queued or running run
GET    /v1/runs/operations   list available operations and their payload schemas
```

Authentication for all five: `Authorization: Bearer <access_token>`.

## Usage

### Submit

The body names an operation key and carries the feature's own request payload. The example uses `stac.validate`, whose payload is the [Validate STAC](validate-stac.md) request:

```bash
curl -X POST "${EPT_API_URL}/v1/runs" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{"operation": "stac.validate", "request": {"stac_url": "'$STAC_URL'"}}'
```

```json
{
  "run_id": "run-3f2c9a1e7b6d4c0a9e8d5f1b2c3d4e5f",
  "operation": "stac.validate",
  "status": "queued",
  "poll_url": "/v1/runs/run-3f2c9a1e7b6d4c0a9e8d5f1b2c3d4e5f"
}
```

Payload validation errors are rejected immediately with `422` (`reason: invalid_run_request`); unknown operation keys with `400` (`reason: unknown_operation`). A run you submit is visible only to you: other callers get `404` for your `run_id`.

### Poll

```bash
curl "${EPT_API_URL}/v1/runs/${RUN_ID}" \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

```json
{
  "run_id": "run-3f2c9a1e7b6d4c0a9e8d5f1b2c3d4e5f",
  "operation": "stac.validate",
  "status": "succeeded",
  "created_at": "2026-10-14T10:00:00Z",
  "started_at": "2026-10-14T10:00:00.123456Z",
  "finished_at": "2026-10-14T10:01:47.654321Z",
  "result": {
    "root_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json",
    "stac_is_valid": true,
    "total_objects_checked": 214
  }
}
```

Statuses: `queued`, `running`, then a terminal `succeeded`, `failed`, or `cancelled`. While `queued`/`running`, `result` and `error` are `null`. When `succeeded`, `result` carries the feature's own response payload — for `stac.validate`, the full [Validate STAC](validate-stac.md) response. When `failed`, `error` carries a plain message; the run still answers `200` (the poll succeeded; the work did not).

Poll a few times a second at most — a short sleep between polls is plenty.

### Cancel

```bash
curl -X DELETE "${EPT_API_URL}/v1/runs/${RUN_ID}" \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

Cancellation is cooperative: the run stops at its next await boundary (an HTTP fetch, a batch hand-off), so a large in-flight step finishes before the status flips to `cancelled`. Cancelling a terminal run changes nothing.

### Python (`httpx`) example

```python
import os
import time

import httpx

headers = {"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"}

submitted = httpx.post(
    f"{os.environ['EPT_API_URL']}/v1/runs",
    headers=headers,
    json={
        "operation": "stac.validate",
        "request": {
            "stac_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json"
        },
    },
)
submitted.raise_for_status()
run_id = submitted.json()["run_id"]

while True:
    status = httpx.get(
        f"{os.environ['EPT_API_URL']}/v1/runs/{run_id}", headers=headers
    ).json()
    if status["status"] in {"succeeded", "failed", "cancelled"}:
        break
    time.sleep(2)

if status["status"] == "succeeded":
    print(f"{status['result']['total_objects_checked']} objects checked")
else:
    print(f"run {status['status']}: {status.get('error')}")
```

## Available operations

| Operation | Feature | Payload | Result |
| --- | --- | --- | --- |
| `stac.validate` | [Validate STAC](validate-stac.md) | The Validate STAC request | The Validate STAC response |
| `arco.stac-item` | [ARCO STAC Item](arco-stac-item.md) | The ARCO STAC Item request | The ARCO STAC Item response |

The authoritative list lives in `ept/features/job_runner/operations.toml`.

## Adding an operation

Operations are declared declaratively; features never import the runner, and the runner imports features only through these declarations. Add one table to `ept/features/job_runner/operations.toml`:

```toml
[operations.<name>]
key = "<feature.operation>"
endpoint = "POST /v1/<feature-path>"   # the sync route taking the same payload
service = "ept.features.<feature>.service:<function>"
request_model = "ept.features.<feature>.models:<RequestModel>"
```

`service` points at the feature's own service function; the runner executes it generically. The service follows one convention (documented at the top of `operations.toml`):

```python
async def <function>(request: <RequestModel>, *, access_token: str | None = None) -> <ResponseModel> | dict:
    """Run the feature; return its response model (dumped by the runner) or a JSON-safe dict."""
```

Token-attachment policy belongs inside the service, so the sync endpoint and background runs behave identically. Services create their own HTTP clients — the submit request's client is closed by the time the run executes. Declarations are imported eagerly at startup, so a broken entry fails loudly instead of on first submit.

## Good to know

- Runs live in the API process memory. They do not survive a restart, and with multiple EPT replicas behind one router a run is visible only on the replica that accepted it. Keep this in mind for deployments; scale-out needs a shared run store.
- The caller's access token is captured at submit time and used while the run executes. If the run outlives the token's validity and the operation needs the token (e.g. an EDITO STAC API URL), later fetches may fail — resubmit in that case.
- Terminal runs are retained for about an hour (and capped), after which their `run_id` answers `404`.
