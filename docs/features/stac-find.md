# STAC Find

Six small find/scan primitives over the EDITO STAC API
(`https://api.dive.edito.eu/data`). Each is one authenticated call (catalog
finding walks the catalog tree and so makes one call per catalog visited);
results are returned close to upstream, wrapped in EPT response models. All
require `Authorization: Bearer <access_token>`.

These are EPT's own find primitives, deliberately *not* named "search": STAC
API reserves `GET|POST /search` for conformant Item Search, and these scans
are not conformant to it. Item finding does use the official
`GET /search` endpoint under the hood.

## Endpoints

| Endpoint | Scans | Free-text filter |
|----------|-------|------------------|
| `GET /v1/stac/find/items` | STAC Item Search (`GET /search`) with `bbox`, `datetime`, `collections`, `ids`, `limit` | `q` narrows on item id/title/description |
| `GET /v1/stac/find/items/scientific` | Item Search filtered/fallback-scanned for STAC Scientific metadata (`sci:doi`, `sci:citation`) | `doi` and/or `citation` (at least one required), `collections`, `limit` (default 100) |
| `GET /v1/stac/find/collections` | `GET /collections`, following `next` pages | `q` over id/title/description/keywords |
| `GET /v1/stac/find/catalogs` | The catalog tree from the projects root (`/catalogs/projects`), following `rel="child"` links recursively (breadth-first with concurrent level fetches, href-deduplicated, depth-capped) | `q` over link title/href |
| `GET /v1/stac/find/assets` | Item find; returns a FeatureCollection of items with a matching asset key/href/title, each item keeping only its matched assets | `q` (required) |
| `GET /v1/stac/find/properties` | Item find; keeps items whose properties match every `match` pair, e.g. `match=platform=Sentinel-1,processing-level=L2A` | `match` (required, case-insensitive values) |

## Examples

```bash
curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/stac/find/items?bbox=2.5,51,4,53&datetime=2024-01-01T00:00:00Z/2024-12-31T23:59:59Z&limit=10"

curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/stac/find/collections?q=bathymetry"

curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/stac/find/assets?q=thumbnail&limit=50"

curl -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  "${EPT_API_URL}/v1/stac/find/properties?match=platform=Sentinel-1&collections=coll-a"
```

## Notes

- `bbox` is `minx,miny,maxx,maxy` (or 6 values with altitude); invalid shapes
  are rejected with `422`.
- Upstream failures surface as the standard Problem Details responses for the
  EDITO STAC API (502 unavailable / upstream error response).
- The primitives behind these routes (`find_item`, `find_collection`,
  `find_catalog`, `find_asset`, `find_properties` in
  `ept/features/stac_find/service.py`) are reusable directly by other
  features. `EditoStacApiClient.get_json()` backs the `GET /search` call and
  is available to any feature.
