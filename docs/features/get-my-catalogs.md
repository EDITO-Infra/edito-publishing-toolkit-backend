# Get my catalogs

List the IDs of the EDITO STAC project catalogs available to the authenticated user. Use it to find the `catalog_id` values accepted by the [publish](publish-stac.md) and [remove](remove-stac.md) features.

## Endpoint

```text
GET /v1/edito/stac/mycatalogs
```

Authentication: `Authorization: Bearer <access_token>`. A refresh token is not required.

## Usage

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/edito/stac/mycatalogs"
```

Example response:

```json
[
  "projects/demo",
  "projects/demo/catalog-1"
]
```

To display each full project catalog ID on a separate line:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/edito/stac/mycatalogs" \
  | jq -r '.[]'
```

Example output:

```text
projects/my-project
projects/my-project/catalog-1
projects/my-project/catalog-1/catalog-2
```

### Python (`httpx`)

```python
import json
import os
from pathlib import Path

import httpx

workspace = Path("./workspace")
workspace.mkdir(parents=True, exist_ok=True)

response = httpx.get(
    f"{os.environ['EPT_API_URL']}/v1/edito/stac/mycatalogs",
    headers={"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"},
    timeout=30.0,
)
response.raise_for_status()
catalog_ids = response.json()

print(catalog_ids)

with (workspace / "catalog-ids.json").open("w", encoding="utf-8") as output:
    json.dump(catalog_ids, output, indent=2)
```

## Response

The endpoint retrieves the user's typed catalog records from the EDITO STAC API and returns the `id` values beginning with `projects/` as an array of strings.

## Good to know

- The `catalog_id` field of the publish and remove requests accepts these IDs:
  - To publish under a project catalog, pass `projects/{project-id}`.
  - To remove a catalog, pass an ID such as `projects/{project-id}/catalog-1` or `projects/{project-id}/catalog-1/catalog-2`.
- The endpoint already returns a JSON array of catalog IDs. Save the response to a file with `curl ... --output catalog-ids.json`, or pretty-print it with `| jq '.' > catalog-ids.json`.
