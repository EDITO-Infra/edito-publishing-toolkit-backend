# Publish STAC

Queue a publication job that publishes a STAC Catalog into the EDITO STAC API under a project catalog. The job runs asynchronously; check the status or the detailed logs using [Publication jobs](publication-jobs.md).

## Endpoint

```text
POST /v1/edito/stac/publish
```

Authentication: `Authorization: Bearer <access_token>` **and** `X-EDITO-Refresh-Token: <refresh_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/edito/stac/publish" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-EDITO-Refresh-Token: ${REFRESH_TOKEN}" \
  -d '{
    "remote_stac_url": "https://minio.dive.edito.eu/oidc-myusername/mystac/catalog.json",
    "catalog_id": "projects/{my-project-id}"
  }'
```

### Python (`httpx`)

```python
import os

import httpx

response = httpx.post(
    f"{os.environ['EPT_API_URL']}/v1/edito/stac/publish",
    headers={
        "Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}",
        "X-EDITO-Refresh-Token": os.environ["REFRESH_TOKEN"],
    },
    json={
        "remote_stac_url": "https://minio.dive.edito.eu/oidc-myusername/mystac/catalog.json",
        "catalog_id": "projects/demo",
    },
    timeout=30.0,
)
response.raise_for_status()
print(response.json()["job_id"])
```

## Request

The public request accepts only `remote_stac_url` and `catalog_id`:

- `remote_stac_url` — URL of the STAC catalog to publish, for example in your EDITO object storage.
- `catalog_id` — the EDITO project catalog to publish into, such as `projects/{project-id}`. Use [Get my catalogs](get-my-catalogs.md) to list the IDs available to you.

## Response

Responds `202 Accepted`:

```json
{
  "job_id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "status": "queued"
}
```

Use the returned `job_id` with [publication-jobs](./publication-jobs.md).

## Good to know

- A refresh token is required because an access token may expire before the queued job is processed.
- To replace a project catalog completely, remove it first with `POST /v1/edito/stac/remove`, then publish the replacement.
