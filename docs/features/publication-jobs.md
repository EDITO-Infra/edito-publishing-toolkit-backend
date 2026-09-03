# Monitor a Publication Job

Use the job ID returned by a publish or removal request to inspect the job's
status, result summary, and logs through EPT.

This page documents EPT's public API, not the Publisher API directly. EPT
forwards the request to the Publisher, presents log pages in EPT's response
shape, and rewrites Publisher continuation links as EPT `next` links.

## Authentication

Every request requires an EDITO access token:

```http
Authorization: Bearer <access_token>
```

This is a bodyless `GET`, so `Content-Type` is not required. Do not send
`X-EDITO-Refresh-Token`. In interactive API documentation, authorize with the
access token only; the `Bearer` prefix is added automatically.

## How the endpoint works

`GET /v1/edito/publish/jobs/{job_id}` returns the lifecycle summary by default.
Poll this endpoint while the job has status `queued` or `running`. The terminal
statuses are `succeeded`, `failed`, `cancelled`, and `interrupted`; use the
publisher-provided `message` and, when needed, log views to diagnose a terminal
failure.

| Query | Result |
| --- | --- |
| none or `?view=summary` | Minimal job lifecycle summary and publisher message |
| `?view=detail` | Summary plus one page of readable, normalized logs |
| `?view=raw` | Summary plus one page of canonical redacted raw logs |
| `limit=1..1000` | Maximum records in a log page; default is `100` |
| `cursor=<value>` | Continue from the cursor in the previous response's `next` link |

Unknown query parameters are rejected. `limit` and `cursor` are relevant only
to detail and raw views. Do not substitute the Publisher's `after_id` query
parameter for EPT's `cursor`; follow EPT's `next` link exactly as returned.

## Job summary

```bash
curl "${EPT_API_URL}/v1/edito/publish/jobs/${JOB_ID}" \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

```json
{
  "id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "type": "stac_publish",
  "status": "succeeded",
  "username": "alice",
  "created_at": "2026-07-01T10:00:00Z",
  "started_at": "2026-07-01T10:00:01Z",
  "finished_at": "2026-07-01T10:00:03Z",
  "message": "Job completed successfully."
}
```

## Log pages

```bash
curl "${EPT_API_URL}/v1/edito/publish/jobs/${JOB_ID}?view=detail&limit=100" \
  --header "Authorization: Bearer ${ACCESS_TOKEN}"
```

The summary fields remain at the top level. Log views add `logs`, `total`,
`limit`, `next`, and `page_message`:

```json
{
  "id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "type": "stac_publish",
  "status": "succeeded",
  "message": "Job completed successfully.",
  "logs": [],
  "total": 2400,
  "limit": 100,
  "page_message": "Returned the first page of job events.",
  "next": "/v1/edito/publish/jobs/8d7d5a93-ff52-4a8e-9eb6-0d76d874b670?view=detail&limit=100&cursor=1071"
}
```

Follow `next` exactly as returned, using the same EPT base URL and access
token. The final page has `"next": null`.

EPT translates its public `cursor` to the Publisher's `after_id` parameter and
rewrites the upstream continuation URL as an EPT `next` link. Clients should
treat the cursor as a continuation value and follow `next` rather than construct
cursor values themselves.

## Export all raw logs as NDJSON

The following examples follow every `next` link and produce one compact JSON
object per line. Each raw event is tagged with its `job_id`, making the file
self-describing when it is moved, combined, or ingested elsewhere. Both examples
write to a temporary file first, so an incomplete export is never presented as a
finished `.ndjson` file.

### Bash

```bash
set -euo pipefail

output="publication-job-${JOB_ID}.raw.ndjson"
temporary="${output}.partial"
next="/v1/edito/publish/jobs/${JOB_ID}?view=raw&limit=100"
rm -f "${temporary}"
trap 'rm -f "${temporary}"' EXIT

while [ -n "${next}" ]; do
  page=$(curl --fail --silent --show-error "${EPT_API_URL}${next}" \
    --header "Authorization: Bearer ${ACCESS_TOKEN}")
  printf '%s\n' "${page}" \
    | jq -c --arg job_id "${JOB_ID}" '.logs[] | . + {job_id: $job_id}' \
    >> "${temporary}"
  next=$(printf '%s\n' "${page}" | jq -r '.next // empty')
done

mv "${temporary}" "${output}"
trap - EXIT
printf 'Exported raw events to %s\n' "${output}"
```

For a single page, the essential operation is:

```bash
curl --fail --silent --show-error \
  "${EPT_API_URL}/v1/edito/publish/jobs/${JOB_ID}?view=raw" \
  --header "Authorization: Bearer ${ACCESS_TOKEN}" \
  | jq -c --arg job_id "${JOB_ID}" '.logs[] | . + {job_id: $job_id}' \
  > "publication-job-${JOB_ID}.raw.ndjson"
```

## Python (`httpx`)

```python
import json
import os
from pathlib import Path

import httpx

workspace = Path("./workspace")
workspace.mkdir(parents=True, exist_ok=True)

access_token = os.environ["ACCESS_TOKEN"]
base_url = os.getenv("EPT_API_URL", "http://localhost:8000")
job_id = os.environ["JOB_ID"]
output_path = workspace / f"publication-job-{job_id}.raw.ndjson"
temporary_path = output_path.with_suffix(output_path.suffix + ".partial")
headers = {"Authorization": f"Bearer {access_token}"}

try:
    with httpx.Client(base_url=base_url, headers=headers, timeout=30.0) as client:
        summary = client.get(f"/v1/edito/publish/jobs/{job_id}")
        summary.raise_for_status()
        print(summary.json()["status"])

        next_url = f"/v1/edito/publish/jobs/{job_id}?view=raw&limit=100"
        with temporary_path.open("w", encoding="utf-8") as output:
            while next_url:
                response = client.get(next_url)
                response.raise_for_status()
                page = response.json()

                for event in page["logs"]:
                    event["job_id"] = job_id
                    output.write(
                        json.dumps(
                            event,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )

                next_url = page["next"]

    temporary_path.replace(output_path)
    print(f"Exported raw events to {output_path}")
finally:
    temporary_path.unlink(missing_ok=True)
```
