# STAC Fetch

Fetch a single STAC object (Item, Collection, or Catalog) by URL and get it back as JSON with its detected type. Companion to the STAC builder: load an existing object into the build form for editing.

## Endpoint

```text
GET /v1/stac/get
```

Authentication: `Authorization: Bearer <access_token>`. The bearer token is attached only when the URL targets the EDITO STAC API — public sources (e.g. S3-hosted catalogs) reject bearer tokens, so it is never sent anywhere else.

## Usage

```bash
curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/stac/get?url=https%3A%2F%2Fminio.dive.edito.eu%2Foidc-myusername%2Fmy-project%2Fitem-1.json" | jq .
```

### Python (`httpx`)

```python
import os

import httpx

response = httpx.get(
    f"{os.environ['EPT_API_URL']}/v1/stac/get",
    headers={"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"},
    params={"url": "https://minio.dive.edito.eu/oidc-myusername/my-project/item-1.json"},
    timeout=120.0,
)
response.raise_for_status()
print(response.json()["object_type"])
```

## Request

One required query parameter:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string (URL) | yes | Remote URL of the STAC object (JSON). Max 2048 characters. |

```text
?url=https://minio.dive.edito.eu/oidc-myusername/my-project/item-1.json
```

The URL must be a public `http(s)` URL without embedded credentials and must not point at a private network address — unsafe targets are rejected. A DNS-level check also runs at fetch time, and every redirect hop is re-validated.

## Response

```json
{
  "source": "https://minio.dive.edito.eu/oidc-myusername/my-project/item-1.json",
  "object_type": "item",
  "stac_object": {
    "type": "Feature",
    "stac_version": "1.1.0",
    "id": "item-1",
    "geometry": {"type": "Point", "coordinates": [3.2, 51.4]},
    "properties": {"datetime": "2024-06-01T00:00:00Z"},
    "assets": {"data": {"href": "https://minio.dive.edito.eu/.../data.nc"}},
    "links": []
  }
}
```

- `source` — the URL the object was fetched from.
- `object_type` — detected type: `item`, `collection`, `catalog`, `item_collection`, or `unknown`.
- `stac_object` — the STAC object as JSON, ready for the builder form.

## Good to know

- The "EDITO STAC API" is matched against `https://api.dive.edito.eu/data` (or the `EDITO_STAC_API_URL` setting on your EPT deployment) — a URL equal to it or under it gets your token.
- A fetch failure or a response that is not valid JSON surfaces as `502` with a detail message; an unsafe or malformed `url` is rejected with `422` before any request is made.
- The object is returned as-is: no validation, no rewriting of links. Run it through [validate](validate-stac.md) if you want a compliance check first.
