# Get my catalogs

List the IDs of the EDITO STAC project catalogs available to the authenticated user:

```text
GET /v1/edito/stac/mycatalogs
```

Retrieves the user's typed catalog records from the EDITO STAC API and returns the `id` values beginning with `projects/` as an array of strings.
It requires `Authorization: Bearer <access_token>`; a refresh token is not required.


## List catalog IDs you have access to

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

## Use project catalog IDs

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

The publish and remove request field is named `catalog_id` and accepts
this catalog ID.

To publish under a project catalog, pass `projects/{project-id}`.

To remove a catalog from a project catalog, pass an ID such as
`projects/{project-id}/catalog-1` or `projects/{project-id}/catalog-1/catalog-2`.

## Export catalogs to JSON

The endpoint already returns a JSON array of catalog IDs. Save the response to a file with:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/edito/stac/mycatalogs" \
  --output catalog-ids.json
```

To save pretty-printed JSON with `jq`:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/edito/stac/mycatalogs" \
  | jq '.' > catalog-ids.json
```

## Python (`httpx`)

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

This endpoint can be used to find a catalog IDs your have access to use for the [publish-stac](publish-stac.md) and [remove-stac](remove-stac.md) features.

 - [publish-stac](publish-stac.md) 
 - [remove-stac](remove-stac.md)
