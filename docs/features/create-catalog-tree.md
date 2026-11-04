# Create Catalog Tree

Create one full STAC Catalog tree — the "create catalog from collections" and "create full catalog from catalogs/collections" capabilities in one route; the inputs determine the shape. Collections become direct children; catalog sources contribute their collection children, which are pulled up as direct children of the new catalog (the source catalog object itself is not copied). EPT derives the catalog metadata, fetches item files via their `item` links, rewrites every link relative to the new tree, and schema-validates each object — no hand-written `children` links anywhere.

## Endpoint

```text
POST /v1/stac/catalogs/tree
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/catalogs/tree" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
        "collections": [{"type": "Collection", "id": "coll-a"}, {"type": "Collection", "id": "coll-b"}],
        "title": "North Sea Bathymetry"
      }' | jq .
```

## Request

Provide at least one source — `collections`, `collection_links`, `catalogs`, `catalog_links`, or `source_prefix` — or a `base` catalog. `source_prefix` is mutually exclusive with the other sources. Unknown fields are rejected (`extra="forbid"`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `collections` | array of objects | no | `[]` | STAC Collections that become direct children of the new catalog. Every entry must have `type == "Collection"`. In preview mode only their metadata matters; child links are wired at export time. |
| `collection_links` | array of strings | no | `[]` | STAC Collection JSON locations to fetch — http(s) URLs (SSRF-guarded), `s3://` URIs, or paths on the EPT filesystem. Each must be a valid Collection; its item files are fetched via its `item` links. Unreadable links are reported as warnings and skipped. |
| `catalogs` | array of objects | no | `[]` | STAC Catalogs that contribute their collection children as direct children of the new catalog. Their collection children are fetched via their `collection`/`child` links. Every entry must have `type == "Catalog"`. |
| `catalog_links` | array of strings | no | `[]` | STAC Catalog JSON locations to fetch (http(s) URLs, `s3://` URIs, or EPT filesystem paths); each contributes its collection children, pulled up as direct children of the new catalog. This is the "put existing catalogs and collections together into one STAC" case. |
| `source_prefix` | string | no | `null` | Local directory or `s3://bucket/prefix` whose subfolders each hold one self-contained collection tree (`collection.json` plus item files), as exported by the [collection tree route](create-collection-tree.md). Each collection is copied under `<target>/<id>/` (id slugified when unsafe as a folder name) with links re-pointed at the new catalog. Mutually exclusive with the other sources; always exports, so `target` (or `local_output`) is required. |
| `base` | object | no | `null` | Existing STAC Catalog or Collection to copy metadata from. Must have an `id`. Its id's last segment prefixes the proposed id — combine with your existing EDITO catalog from `GET /v1/edito/stac/mycatalogs` for a unique flat id. Without a base, the first catalog source seeds title/description. |
| `id` | string | no | `null` | Explicit catalog id. Ids are flat: no `/` or `\`, no empty/`.`/`..` segments. When omitted, one is proposed (see below). |
| `title` | string | no | `null` | Human-readable title; also seeds the id slug. |
| `description` | string | no | `null` | Catalog description. |
| `bucket` | string | no | `null` | Destination S3 bucket. Defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token; project buckets you can access are equally valid. Must be a bucket name without `s3://` or an object key. |
| `target` | string | no | `null` | Destination folder: an S3 prefix within `bucket`. `catalog.json` is written at the root, each source under `<id>/`. Required with `source_prefix` (unless `local_output` is set). Omit to preview only. |
| `local_output` | string | no | `null` | Write to this directory on the EPT filesystem instead of S3. Takes precedence over `target`/`bucket`. |
| `overwrite` | bool | no | `false` | Replace an existing `catalog.json` and collection copies. With `overwrite=false`, existing `catalog.json` or `collection.json` files raise `409`; item files are always (re-)written. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | string | no | `null` | Request-scoped S3 credentials for `s3://` sources/targets; falls back to the service-level `S3_*` environment when omitted. Provide all three credential fields or none. |

Id proposal: an explicit `id` always wins. With a base it is `<base id>-<slug>`; otherwise `<username>-<slug>` from your bearer token. The slug is the title (or base title) lowercased with non-alphanumeric runs collapsed to `-`, falling back to `catalog`.

## Response

```json
{
  "stac_object": {
    "type": "Catalog",
    "stac_version": "1.1.0",
    "id": "myusername-north-sea-bathymetry",
    "title": "North Sea Bathymetry",
    "description": "North Sea Bathymetry",
    "links": []
  },
  "warnings": [
    "id proposed from your username and title slug: 'myusername-north-sea-bathymetry' — it must be unique among the project catalogs you own (GET /v1/edito/stac/mycatalogs); EDITO adds its infrastructure root at publish time",
    "2 child collection(s) noted: coll-a, coll-b",
    "description fell back to title/id: set an explicit description in the builder form"
  ],
  "status": "preview",
  "exported": false,
  "catalog_uri": null,
  "collection_uris": [],
  "item_uris": [],
  "objects": []
}
```

- `stac_object` — the derived catalog: `type`, `stac_version` 1.1.0, `id`, `description`, and `title`/`stac_extensions` carried over from the base when present.
- `warnings` — derivation notes: how the id was proposed, which child collections were noted, keywords/providers unions, and what fell back to defaults.
- `status` — `preview` (nothing written) or `completed` (exported).
- On export, `catalog_uri` is the written root `catalog.json`; `collection_uris`/`item_uris` list every written collection and item file; `objects` carries one report per written object — `path`, `object_type`, `object_id`, and `validation_issues` (empty list = schema-valid).

## Good to know

- Ids must be unique and flat (`<username>-<slug>`); EDITO adds its infrastructure root at publish time, so you never put `/` in the id.
- The description falls back to the base's, then the title, then the id — a warning tells you when to set an explicit one.
- Catalog sources contribute their collection children as direct children of the new catalog; the source catalog object itself is not copied, and catalogs found *below* that level are skipped with a warning (pass that catalog as its own source to combine it).
- Collection ids must be unique among direct children (and within each sub-catalog); duplicates are rejected with `400`.
- Malformed requests (bad base, invalid explicit id, unknown fields) fail with `422`; id-derivation failures (no username in the token) surface as `400`; export conflicts with `409`.
