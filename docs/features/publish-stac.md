# Publish STAC Usage

Queue a publication job that publishes a STAC Catalog into the
EDITO STAC API under a project catalog.

The public request accepts only `remote_stac_url` and `catalog_id`.

This endpoint requires both `Authorization: Bearer <access_token>` and
`X-EDITO-Refresh-Token: <refresh_token>`.

Note: A refresh token is required to queue a publication job in the publishing service because an access token
may expire before the job is processed.

To see catalogs available for publishing, the [Get my catalogs](get-my-catalogs.md) endpoint can be used.

To replace a project catalog completely, remove it first with
`POST /v1/edito/stac/remove`, then publish the replacement.
  
## API

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

Example response:

```json
{
  "job_id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "status": "queued"
}
```

Use the returned `job_id` with `GET /v1/edito/publish/jobs/{job_id}`.
