# Build a STAC Item

Build a STAC Item from individual fields, from a complete JSON object, or from an existing item fetched by URL — provided fields override the base. Preview the JSON, or export it as `item.json` to S3 or a local folder, ready for [assemble](assemble-stac.md) to stitch into a tree.

## Endpoint

```text
POST /v1/stac/build/item
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/build/item" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
    "id": "observations-2025-01",
    "bbox": [2.5, 51.0, 4.5, 51.6],
    "datetime": "2025-01-15T10:30:00Z",
    "collection": "observations-2025",
    "properties": {"platform": "sentinel-3a"},
    "assets": {
      "data": {
        "href": "s3://oidc-<username>/catalog-1/observations-2025-01.zarr",
        "type": "application/vnd+zarr",
        "roles": ["data"]
      }
    },
    "links": [
      {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
      {"rel": "parent", "href": "s3://oidc-<username>/catalog-1/collection.json"}
    ],
    "target": "catalog-1"
  }'
```

## Request

Build from fields — `id` plus temporal and spatial info are what the STAC spec demands:

```json
{
  "id": "observations-2025-01",
  "bbox": [2.5, 51.0, 4.5, 51.6],
  "datetime": "2025-01-15T10:30:00Z"
}
```

For a time interval instead of an instant, use `start_datetime` + `end_datetime` (each requires the other). `geometry` is derived from `bbox` automatically, and vice versa.

Edit mode — start from an existing object; explicitly provided fields override it:

```json
{
  "source_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/item.json",
  "title": "Revised title"
}
```

Export fields are identical to the [catalog builder](stac-build-catalog.md): `bucket`, `target` (`item.json` is appended automatically; omit to preview only), `local_output` (`false`), `overwrite` (`false`), and the optional S3 connection fields `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token`.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `id` | string | — | Required without a base object; pattern `^[A-Za-z0-9._\-~]+$` |
| `stac_json` | object | — | Complete item used as the base object |
| `source_url` | URL | — | Fetch the base object (max 2048 chars, SSRF-guarded, public http(s) only) |
| `stac_extensions` | list[string] | `[]` | Extension schema URLs |
| `title` / `description` | string | — | Stored in item `properties` |
| `geometry` | object | from `bbox` | GeoJSON geometry |
| `bbox` | list[float] | from `geometry` | 4 or 6 coordinates |
| `datetime` | string | — | ISO 8601 instant |
| `start_datetime` / `end_datetime` | string | — | ISO 8601 interval; must be given as a pair |
| `properties` | object | `{}` | Extra STAC properties; explicit title/description/datetime fields win on conflict |
| `collection` | string | — | Id of the collection this item belongs to |
| `assets` | object | `{}` | Named assets; each value needs at least `href` |
| `links` | list[object] | `[]` | Standard STAC links; each entry needs non-empty `rel` and `href` |
| `extra_fields` | object | `{}` | Arbitrary top-level fields merged in last |

Unknown fields are rejected (`extra="forbid"`).

## Response

```json
{
  "stac_object": {
    "type": "Feature",
    "stac_version": "1.1.0",
    "id": "observations-2025-01",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[2.5, 51.0], [4.5, 51.0], [4.5, 51.6], [2.5, 51.6], [2.5, 51.0]]]
    },
    "bbox": [2.5, 51.0, 4.5, 51.6],
    "properties": {
      "datetime": "2025-01-15T10:30:00Z",
      "platform": "sentinel-3a"
    },
    "collection": "observations-2025",
    "assets": {
      "data": {
        "href": "s3://oidc-myusername/catalog-1/observations-2025-01.zarr",
        "type": "application/vnd+zarr",
        "roles": ["data"]
      }
    },
    "links": [
      {"rel": "root", "href": "s3://oidc-myusername/catalog-1/catalog.json"},
      {"rel": "parent", "href": "s3://oidc-myusername/catalog-1/collection.json"},
      {"rel": "collection", "href": "./collection.json"},
      {"rel": "self", "href": "s3://oidc-myusername/catalog-1/item.json"}
    ]
  },
  "warnings": [],
  "exported": true,
  "target_uri": "s3://oidc-myusername/catalog-1/item.json",
  "status": "completed"
}
```

- `stac_object` — the built item as JSON.
- `warnings` — structural findings (e.g. no root link, no temporal info, neither geometry nor bbox, `bbox` with other than 4 or 6 coordinates).
- `exported` / `target_uri` — set when `target` was given; `status` is `completed`, otherwise `preview`.

## Good to know

- An item needs `datetime` or `start_datetime` + `end_datetime`. Building from fields without any temporal info fails with `400`; an overlaid base item without temporal info is accepted but warned about.
- Setting only `geometry` derives the `bbox` (and vice versa); neither is required at request time, but an item with neither gets a warning.
- If `collection` is set but no `rel=collection` link is, a `collection.json` sibling link is synthesized — the STAC 1.1 schema requires it.
- `title`, `description` and the datetime fields are stored inside `properties`. `datetime` given as an instant is normalized by pystac; interval fields go into properties directly.
- Non-ISO 8601 timestamps, malformed assets/geometry, or an unusable `target` return `400`. An existing target returns `409` unless `overwrite=true`; a failed base-object fetch returns `502`.
- Link hrefs are plain and need not exist yet — [assemble](assemble-stac.md) can stitch the tree afterwards. [Validate](validate-stac.md) the tree before [publishing](publish-stac.md).
