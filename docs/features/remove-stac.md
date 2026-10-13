# Remove STAC

Queue removal of a project catalog from the EDITO STAC API. The job runs asynchronously; check the status or the detailed logs using [Publication jobs](publication-jobs.md).

## Endpoint

```text
POST /v1/edito/stac/remove
```

Authentication: `Authorization: Bearer <access_token>` **and** `X-EDITO-Refresh-Token: <refresh_token>`.

## Usage

Once you have the catalog ID (see [Get my catalogs](get-my-catalogs.md)), use it directly in the request:

```bash
curl -X POST "${EPT_API_URL}/v1/edito/stac/remove" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-EDITO-Refresh-Token: ${REFRESH_TOKEN}" \
  -d '{
    "catalog_id": "projects/{project-id}/{catalog-to-remove}"
  }'
```

### Python (`httpx`)

```python
import os

import httpx

response = httpx.post(
    f"{os.environ['EPT_API_URL']}/v1/edito/stac/remove",
    headers={
        "Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}",
        "X-EDITO-Refresh-Token": os.environ["REFRESH_TOKEN"],
    },
    json={"catalog_id": f"{os.environ['REMOVE_CATALOG_ID']}"},
    timeout=30.0,
)
response.raise_for_status()
print(response.json()["job_id"])
```

## Request

The public request accepts only `catalog_id` — the ID of the catalog to remove, such as `projects/{project-id}/catalog-1`.

## Response

```json
{
  "job_id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "status": "queued"
}
```

Use the returned `job_id` with `GET /v1/edito/publish/jobs/{job_id}`.

## Good to know

- Removal is destructive: the catalog and its contents are removed from the EDITO STAC API.
- A refresh token is required because an access token may expire before the queued job is processed.
