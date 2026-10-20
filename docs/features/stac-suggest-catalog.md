# Suggest STAC Catalog

Derive a STAC Catalog from a set of collections (or an existing base catalog): a unique, flat id is proposed for you and metadata is carried over from the base. The suggested JSON is schema-validated and shaped for the STAC Catalog builder — review it there, then build/export. Pure metadata: no storage access.

## Endpoint

```text
POST /v1/stac/suggest/catalog
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl -X POST "${EPT_API_URL}/v1/stac/suggest/catalog" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -d '{
        "collections": [{"type": "Collection", "id": "coll-a"}, {"type": "Collection", "id": "coll-b"}],
        "title": "North Sea Bathymetry"
      }' | jq .
```

## Request

All fields are optional individually, but you must provide `collections` or `base` (otherwise the request is rejected with `400`). Unknown fields are rejected (`extra="forbid"`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `collections` | array of objects | no | `[]` | STAC Collections that will become the catalog's children. Every entry must have `type == "Collection"`. Only their presence/ids matter here; child links are wired at build or assemble time. |
| `base` | object | no | `null` | Existing STAC Catalog or Collection to copy metadata from. Must have an `id`. Its id's last segment prefixes the proposed id — combine with your existing EDITO catalog from `GET /v1/edito/stac/mycatalogs` for a unique flat id. |
| `id` | string | no | `null` | Explicit catalog id. Ids are flat: no `/` or `\`, no empty/`.`/`..` segments. When omitted, one is proposed (see below). |
| `title` | string | no | `null` | Human-readable title; also seeds the id slug. |
| `description` | string | no | `null` | Catalog description. |

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
    "links are empty: root and child links are wired by the STAC builder or assemble",
    "description fell back to title/id: set an explicit description in the builder form"
  ],
  "status": "suggested"
}
```

- `stac_object` — the suggested catalog: `type`, `stac_version` 1.1.0, `id`, `description`, and `title`/`stac_extensions` carried over from the base when present.
- `warnings` — derivation notes for the builder form: how the id was proposed, which child collections were noted, and what fell back to defaults.
- `status` — `suggested`.

## Good to know

- Ids must be unique and flat (`<username>-<slug>`); EDITO adds its infrastructure root at publish time, so you never put `/` in the id.
- The description falls back to the base's, then the title, then the id — a warning tells you when to set an explicit one.
- Child `links` are always empty here: the STAC builder or [assemble](assemble-stac.md) wires root and child links once real hrefs exist.
- Malformed requests (bad base, invalid explicit id, unknown fields) fail with `422`; id-derivation failures (no username in the token) surface as `400`.
