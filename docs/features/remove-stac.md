# Remove STAC Usage

Queue removal of a project catalog from the EDITO STAC API.

The public request accepts only `catalog_id`. 

This endpoint requires both `Authorization: Bearer <access_token>` and
`X-EDITO-Refresh-Token: <refresh_token>`.

Note: A refresh token is required to queue a removal job in the publishing service because an access token
may expire before the job is processed.

See feature [Get my catalogs](get-my-catalogs.md) for how to get catalog IDs available to you.

Once you have the catalog ID, you can set it as an environment variable and use it to queue the removal job. Or use it directly in the API request.

```bash
export REMOVE_CATALOG_ID="projects/{project-id}/{catalog-to-remove}"
```

## API

```bash
curl -X POST "${EPT_API_URL}/v1/edito/stac/remove" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-EDITO-Refresh-Token: ${REFRESH_TOKEN}" \
  -d '{
    "catalog_id": "${REMOVE_CATALOG_ID}"
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

Example response:

```json
{
  "job_id": "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670",
  "status": "queued"
}
```

Use the returned `job_id` with `GET /v1/edito/publish/jobs/{job_id}`.
