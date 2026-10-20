# Build a STAC Catalog

Build a STAC Catalog from individual fields, from a complete JSON object, or from an existing catalog fetched by URL — provided fields override the base. Preview the JSON, or export it as `catalog.json` to S3 or a local folder, ready for [assemble](assemble-stac.md) to stitch into a tree.

## Endpoint

```text
POST /v1/stac/build/catalog
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/build/catalog" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
    "id": "catalog-1",
    "title": "My catalog",
    "description": "Root catalog for my datasets.",
    "links": [
      {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
      {"rel": "child", "href": "s3://oidc-<username>/catalog-1/collection.json"}
    ],
    "target": "catalog-1"
  }'
```

## Request

Build from fields — `id` is the only required field (it may be omitted only when `stac_json` or `source_url` provides the base):

```json
{
  "id": "catalog-1",
  "title": "My catalog",
  "description": "Root catalog for my datasets."
}
```

Edit mode — start from an existing object; explicitly provided fields override it:

```json
{
  "source_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json",
  "title": "Renamed catalog"
}
```

Export fields (all optional):

```json
{
  "bucket": "oidc-myusername",
  "target": "catalog-1",
  "local_output": false,
  "overwrite": false,
  "endpoint_url": "https://minio.dive.edito.eu",
  "access_key_id": "...",
  "secret_access_key": "...",
  "session_token": "..."
}
```

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `id` | string | — | Required without a base object; pattern `^[A-Za-z0-9._\-~]+$` |
| `stac_json` | object | — | Complete catalog used as the base object |
| `source_url` | URL | — | Fetch the base object (max 2048 chars, SSRF-guarded, public http(s) only) |
| `stac_extensions` | list[string] | `[]` | Extension schema URLs |
| `title` | string | — | Human-readable title |
| `description` | string | — | Falls back to title, then id when omitted |
| `links` | list[object] | `[]` | Standard STAC links; each entry needs non-empty `rel` and `href` |
| `extra_fields` | object | `{}` | Arbitrary top-level fields merged in last |
| `bucket` | string | your `oidc-<username>` bucket | Bucket name only — no `s3://`, no `/` |
| `target` | string | — | Destination folder (S3 prefix, or local folder with `local_output`); omit to preview only |
| `local_output` | string or `false` | `false` | Directory on the EPT filesystem to write to instead of S3; `false` means not set |
| `overwrite` | bool | `false` | Replace an existing target |
| `endpoint_url` / `access_key_id` / `secret_access_key` / `session_token` | string | service `S3_*` env | For `s3://` targets; the first three must be given together |

Unknown fields are rejected (`extra="forbid"`).

## Response

```json
{
  "stac_object": {
    "type": "Catalog",
    "stac_version": "1.1.0",
    "id": "catalog-1",
    "title": "My catalog",
    "description": "Root catalog for my datasets.",
    "links": [
      {"rel": "root", "href": "s3://oidc-myusername/catalog-1/catalog.json"},
      {"rel": "child", "href": "s3://oidc-myusername/catalog-1/collection.json"},
      {"rel": "self", "href": "s3://oidc-myusername/catalog-1/catalog.json"}
    ]
  },
  "warnings": [],
  "exported": true,
  "target_uri": "s3://oidc-myusername/catalog-1/catalog.json",
  "status": "completed"
}
```

- `stac_object` — the built catalog as JSON.
- `warnings` — structural findings that keep it from being fully spec-compliant (e.g. missing root link, missing child links, description falling back to title/id).
- `exported` / `target_uri` — set when `target` was given; `status` is `completed`, otherwise `preview`.

## Good to know

- Omit `target` for a pure preview — nothing is written and `status` is `preview`.
- With S3 export (default), `target` is a prefix inside `bucket`, which defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token. `catalog.json` is appended automatically — do not include it in `target`.
- Exporting to an existing path returns `409` unless `overwrite=true`. Bad input returns `400`; a failed base-object fetch returns `502`.
- When `source_url` points at the EDITO STAC API, your bearer token is attached to the fetch automatically; for other URLs the fetch is anonymous.
- Link hrefs are plain and need not exist yet — [assemble](assemble-stac.md) can stitch the tree afterwards. [Validate](validate-stac.md) the tree before [publishing](publish-stac.md).
