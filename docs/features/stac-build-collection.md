# Build a STAC Collection

Build a STAC Collection from individual fields, from a complete JSON object, or from an existing collection fetched by URL — provided fields override the base. Preview the JSON, or export it as `collection.json` to S3 or a local folder, ready for [assemble](assemble-stac.md) to stitch into a tree.

## Endpoint

```text
POST /v1/stac/build/collection
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/build/collection" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
    "id": "observations-2025",
    "description": "Observations collected in 2025.",
    "license": "CC-BY-4.0",
    "extent": {
      "spatial": {"bbox": [[2.5, 51.0, 4.5, 51.6]]},
      "temporal": {"interval": [["2025-01-01T00:00:00Z", null]]}
    },
    "links": [
      {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
      {"rel": "parent", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
      {"rel": "child", "href": "s3://oidc-<username>/catalog-1/observations-2025-01.json"}
    ],
    "target": "catalog-1"
  }'
```

## Request

Build from fields — `id` is the only required field (it may be omitted only when `stac_json` or `source_url` provides the base):

```json
{
  "id": "observations-2025",
  "description": "Observations collected in 2025.",
  "extent": {
    "spatial": {"bbox": [[2.5, 51.0, 4.5, 51.6]]},
    "temporal": {"interval": [["2025-01-01T00:00:00Z", null]]}
  },
  "license": "CC-BY-4.0",
  "providers": [{"name": "VLIZ", "roles": ["producer"], "url": "https://www.vliz.be"}]
}
```

Edit mode — start from an existing object; explicitly provided fields override it:

```json
{
  "stac_json": {"type": "Collection", "id": "observations-2025", "stac_version": "1.0.0"},
  "title": "Observations 2025 (revised)"
}
```

Export fields are identical to the [catalog builder](stac-build-catalog.md): `bucket`, `target` (append `collection.json` to it automatically; omit to preview only), `local_output` (`false`), `overwrite` (`false`), and the optional S3 connection fields `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token`.

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `id` | string | — | Required without a base object; pattern `^[A-Za-z0-9._\-~]+$` |
| `stac_json` | object | — | Complete collection used as the base object |
| `source_url` | URL | — | Fetch the base object (max 2048 chars, SSRF-guarded, public http(s) only) |
| `stac_extensions` | list[string] | `[]` | Extension schema URLs |
| `title` | string | — | Human-readable title |
| `description` | string | — | Falls back to title, then id when omitted |
| `extent` | object | — | Spatial/temporal extent; without it a world-wide placeholder is used (with a warning) |
| `license` | string | `proprietary` | SPDX license identifier or `proprietary` |
| `providers` | list[object] | `[]` | e.g. `{"name": ..., "roles": [...], "url": ...}` |
| `links` | list[object] | `[]` | Standard STAC links; each entry needs non-empty `rel` and `href` |
| `extra_fields` | object | `{}` | Arbitrary top-level fields merged in last |

Unknown fields are rejected (`extra="forbid"`).

## Response

```json
{
  "stac_object": {
    "type": "Collection",
    "stac_version": "1.1.0",
    "id": "observations-2025",
    "description": "Observations collected in 2025.",
    "license": "CC-BY-4.0",
    "extent": {
      "spatial": {"bbox": [[2.5, 51.0, 4.5, 51.6]]},
      "temporal": {"interval": [["2025-01-01T00:00:00Z", null]]}
    },
    "links": [
      {"rel": "root", "href": "s3://oidc-myusername/catalog-1/catalog.json"},
      {"rel": "parent", "href": "s3://oidc-myusername/catalog-1/catalog.json"},
      {"rel": "child", "href": "s3://oidc-myusername/catalog-1/observations-2025-01.json"},
      {"rel": "self", "href": "s3://oidc-myusername/catalog-1/collection.json"}
    ]
  },
  "warnings": [],
  "exported": true,
  "target_uri": "s3://oidc-myusername/catalog-1/collection.json",
  "status": "completed"
}
```

- `stac_object` — the built collection as JSON.
- `warnings` — structural findings (e.g. missing extent, missing root link, description falling back to title/id).
- `exported` / `target_uri` — set when `target` was given; `status` is `completed`, otherwise `preview`.

## Good to know

- With S3 export, `target` is a prefix inside `bucket` (default: your personal `oidc-<username>` bucket from your bearer token); `collection.json` is appended automatically.
- Exporting to an existing path returns `409` unless `overwrite=true`. Bad input returns `400`; a failed base-object fetch returns `502`.
- When `source_url` points at the EDITO STAC API, your bearer token is attached to the fetch automatically; for other URLs the fetch is anonymous.
- The schema also requires `license` — set an SPDX identifier such as `CC-BY-4.0`, or leave the `proprietary` default.
- Link hrefs are plain and need not exist yet — [assemble](assemble-stac.md) can stitch the tree afterwards. [Validate](validate-stac.md) the tree before [publishing](publish-stac.md).
