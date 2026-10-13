# Validate STAC

Check your STAC metadata before publishing. Give the endpoint the URL of your catalog, collection, or item — it reads it, follows the links to everything inside, and tells you what would block publication on EDITO.

## Endpoint

```text
POST /v1/stac/validate
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/validate" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{"stac_url": "'$STAC_URL'"}' | jq . > catalog-1-validation-results.json
```

### Python (`httpx`)

```python
import json
import os

import httpx

response = httpx.post(
    f"{os.environ['EPT_API_URL']}/v1/stac/validate",
    headers={"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"},
    json={
        "stac_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json"
    },
    timeout=120.0,
)
response.raise_for_status()

with open("catalog-1-validation-results.json", "w") as f:
    json.dump(response.json(), f, indent=4)
```

## Request

The only field you need is `stac_url`:

```json
{
  "stac_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json"
}
```

Three optional overrides (defaults shown):

```json
{
  "stac_url": "https://...",
  "validate_children": true,
  "max_depth": 3,
  "max_objects": 5000
}
```

`validate_children` turns child validation off, `max_depth` caps recursion (1–10), `max_objects` caps how many objects are scanned per run. When the object limit is reached the report includes a `traversal_limit` warning.

## Response

You get a report. Validation problems do **not** cause an error — they are listed in the report:

```json
{
  "is_valid": false,
  "summary": {
    "total_objects": 12,
    "valid_objects": 11,
    "invalid_objects": 1,
    "error_count": 1,
    "warning_count": 0,
    "errors_by_rule": {"edito_asset_href_not_public_https": 1}
  },
  "errors": [
    {
      "object_id": "item-1",
      "location": "https://example.com/item-1.json",
      "rule": "edito_asset_href_not_public_https",
      "message": "EDITO item asset hrefs must be public https:// URLs"
    }
  ],
  "warnings": [],
  "validation_tree": {
    "url": "https://.../catalog.json",
    "object_type": "catalog",
    "object_id": "root-catalog",
    "is_valid": true,
    "children": ["...each object below it, with its own issues..."],
    "issues": []
  }
}
```

How to read it:

- `is_valid` — did the top-level object itself pass? Check `summary` for the whole run.
- `summary` — how many objects were checked, how many passed, and which rules were broken most often.
- `errors` / `warnings` — every finding, with the object, its location, the rule, and a message.
- `validation_tree` — your catalog as a tree, so you can see where each problem sits.

## Good to know

- What is checked:
  - **STAC spec** — every object is validated against the official STAC schemas (rule `stac-core-schema`).
  - **EDITO publishing rules**:
    - `edito_item_not_in_collection` — items must sit inside a collection.
    - `edito_collection_in_collection` — collections nested in collections are not supported.
    - `edito_asset_href_not_public_https` — item asset links must be public http(s) URLs without embedded credentials.
- A link that cannot be read is reported as an error and the scan continues, so one broken link does not hide the rest of the report.
- A typical workflow: [validate](validate-stac.md) first, then [publish](publish-stac.md).
