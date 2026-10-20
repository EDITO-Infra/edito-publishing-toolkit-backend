# Suggest STAC Collection

Derive a STAC Collection from a set of items (or an existing base collection): the extent is the union of the items' bboxes and datetimes, the id is proposed so it is unique and flat, and license/providers/description are carried over from the base. The suggested JSON is schema-validated and shaped for the STAC Collection builder. Purely suggestive by default; give it `target`/`local_output` (or `source_folder`) and it also exports — fetching item JSONs from URLs, S3, or the EPT filesystem when needed.

## Endpoint

```text
POST /v1/stac/suggest/collection
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/suggest/collection" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
        "items": [
          {
            "type": "Feature",
            "id": "item-1",
            "bbox": [2.5, 51.0, 3.5, 52.0],
            "properties": {"datetime": "2024-06-01T00:00:00Z"}
          }
        ],
        "title": "North Sea Bathymetry"
      }' | jq .
```

## Request

Provide at least one item source — `items`, `item_links`, or `source_folder` — or a `base` collection. `source_folder` is mutually exclusive with `items`/`item_links` and with the export fields. Unknown fields are rejected (`extra="forbid"`), as are malformed requests (`422`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `items` | array of objects | no | `[]` | STAC Items to derive the extent from (spatial union of bboxes, temporal union of datetimes). Every entry must have `type == "Feature"`. Optional when `base` is given; the base extent is kept as-is when no items are provided. |
| `item_links` | array of strings | no | `[]` | STAC Item JSON locations to fetch and derive from — http(s) URLs, `s3://` URIs, or paths on the EPT service filesystem. Unreadable links are reported as warnings and skipped. Combined with any inline `items`. |
| `source_folder` | string | no | `null` | Local directory or `s3://bucket/prefix` whose `*.json` files are the collection's items. Mutually exclusive with `items`/`item_links` and with the export fields. Always exports: `collection.json` is written inside the folder and the item files are re-linked in place (collection id, root/parent/collection links). |
| `base` | object | no | `null` | Existing STAC Collection to copy metadata from (title, description, license, providers). Must have `type == "Collection"` and an `id`. Its id's last segment prefixes the proposed id; its extent is recomputed when `items` are given. |
| `id` | string | no | `null` | Explicit collection id. Ids are flat: no `/` or `\`, no empty/`.`/`..` segments. When omitted, one is proposed (see below). |
| `title` | string | no | `null` | Human-readable title; also seeds the id slug. |
| `description` | string | no | `null` | Collection description. |
| `license` | string | no | `null` | SPDX license identifier; defaults to the base's, the items' shared license, or `other`. |
| `bucket` | string | no | `null` | Destination S3 bucket. Defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token; other buckets you can access, e.g. project buckets `project-<projectid>`, are equally valid. Must be a bucket name without `s3://` or an object key. Only valid with `target`. |
| `target` | string | no | `null` | Destination folder: an S3 prefix within `bucket`. `collection.json` plus one file per item is written inside it. Omit to preview only. |
| `local_output` | string | no | `null` | Write to this directory on the EPT filesystem instead of S3. Takes precedence over `target`/`bucket`. |
| `overwrite` | bool | no | `false` | Replace an existing `collection.json`. With `source_folder`, item files are always re-linked in place; `overwrite` only guards `collection.json`. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | string | no | `null` | Request-scoped S3 credentials for `s3://` sources/targets; falls back to the service-level `S3_*` environment when omitted. Provide all three credential fields or none. |

Id proposal: an explicit `id` always wins. With a base it is `<base id>-<slug>`; otherwise `<username>-<slug>` from your bearer token. The slug is the title (or base title, or first item id) lowercased with non-alphanumeric runs collapsed to `-`, falling back to `collection`.

## Response

```json
{
  "stac_object": {
    "type": "Collection",
    "stac_version": "1.1.0",
    "id": "myusername-north-sea-bathymetry",
    "title": "North Sea Bathymetry",
    "description": "North Sea Bathymetry",
    "license": "other",
    "extent": {
      "spatial": {"bbox": [[2.5, 51.0, 3.5, 52.0]]},
      "temporal": {"interval": [["2024-06-01T00:00:00Z", "2024-06-01T00:00:00Z"]]}
    },
    "links": []
  },
  "warnings": [
    "id proposed from your username and title slug: 'myusername-north-sea-bathymetry' — it must be unique within your catalog",
    "license 'other' derived from the items",
    "links are empty: root and item links are wired by the STAC builder or assemble",
    "description fell back to title/id: set an explicit description in the builder form"
  ],
  "status": "suggested",
  "exported": false,
  "collection_uri": null,
  "item_uris": []
}
```

- `stac_object` — the suggested collection: `type`, `stac_version` 1.1.0, `id`, `description`, `license`, `extent` (union of the items), and `title`/`providers`/`stac_extensions` carried over from the base when present.
- `warnings` — derivation notes: how the id was proposed, where the license came from, and what fell back to defaults.
- `status` — `suggested` (preview) or `completed` (exported). When exporting, `exported` is true and `collection_uri`/`item_uris` carry the full locations of the written (or re-linked) `collection.json` and item files.

## Good to know

- Export names are safe: `collection.json` goes to the target root and each item to `<item-id>/<item-id>.json`; ids that are unsafe as file/folder names (spaces, `/`, `..`, …) are slugified for the export (with a warning) while the item id itself stays untouched. Two items slugging to the same name are rejected.
- Extent is a union: `bbox` values of 4 or 6 are accepted (mixed 2D/3D items are projected to 2D), and datetimes come from `properties.datetime` or `properties.start_datetime`/`end_datetime`. Unparseable datetimes are ignored with a warning.
- With no item geometry or datetime, the extent is copied from the base — or becomes a world-wide placeholder (`[-180,-90,180,90]`, open temporal interval) with a warning.
- License is scanned per item in order of authority: top-level or `properties.license`, `properties.licenses[].id`, then SPDX ids recovered from `properties.constraints[].text`. Items disagreeing on the license cannot be merged — the conflict is reported and the default (`other`) is used unless you pass `license` explicitly.
- Item `links` are always empty here: the STAC builder or [assemble](assemble-stac.md) wires root and item links once real hrefs exist.
- Malformed requests (bad base, invalid explicit id, unknown fields) fail with `422`; service-level errors use `400` (derivation failure, e.g. no username in the token), `409` (target exists, `overwrite` false), and `404` (missing source). Upstream fetch failures surface their HTTP status, or `502` when unreachable.
