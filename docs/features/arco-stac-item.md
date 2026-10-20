# ARCO STAC Item

Turn an existing STAC Item's data assets into ARCO formats and write the
updated Item JSON back to S3. The Item is read from a local path, an
`s3://` URI, or an HTTP(S) URL — typically the output of
[ISO19139 to STAC Item](iso-to-stac-item.md).

## Endpoint

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/v1/arco/stac-item` | Bearer access token | See below |

The same pipeline runs as a background job: submit the identical payload to
`POST /v1/runs` with `"operation": "arco.stac-item"` and poll
`GET /v1/runs/{run_id}` — see the [EPT job runner](job-runner.md).

## Declared conversions

Conversions are declared in the feature's `conversions.toml`
(`ept/features/arco_stac_item/conversions.toml`) — the same declarative
mechanism as the [job runner's](job-runner.md) `operations.toml`. Each entry
names a feature service and request model by import string, plus how asset
values map onto the request. Adding a new conversion (e.g. a COG converter)
is a TOML table, not Python.

Shipped conversions:

| Key | Converts | Detects automatically |
| --- | --- | --- |
| `nc-to-zarr` | NetCDF → Zarr store | `application/x-netcdf`, `.nc`/`.nc4`/`.cdf` hrefs, OPeNDAP/DAP/THREDDS URLs |
| `gdb-to-parquet` | File geodatabase → one GeoParquet file per layer | `.gdb` / `.gdb.zip` hrefs |
| `shp-to-parquet` | ESRI shapefile(s) → one GeoParquet file per `.shp` | `.shp` hrefs |
| `upload` | Hosts the asset in S3 unchanged | never auto-detected |

A plain `.zip` is ambiguous and is never auto-converted — map it explicitly.

## How assets are selected

1. **Explicit mappings win.** `conversions` maps an asset key *or* an href
   substring to a declared conversion key:

   ```json
   "conversions": {"downloads/data.zip": "gdb-to-parquet"}
   ```

2. **Auto-detection** fills in everything not mapped explicitly, using the
   `detect` rules of each declared conversion (see table above).

## Output locations

- **Updated Item** — `item_target`: the Item JSON is written to
  `s3://<bucket>/<item_target>/<item_id>.json`. Omit `item_target` to replace
  the Item at its source location in place (S3 sources only); for a link or
  local source the updated Item is written to the default location
  `s3://<bucket>/<item_id>.json` (bucket root).
- **Converted assets** — `assets_target`: all conversion outputs land under
  this prefix (per-asset targets are planned). Defaults to `item_target`
  when set, else to the source Item's folder (S3 sources) or the bucket root.

Each converted asset is added to the Item under its declared asset key
(`zarr`, `parquet`, or the source key for `upload`) with `roles: ["data"]`;
colliding keys get a numeric suffix (`zarr-2`, ...). An `upload` re-points
its own source asset key to the hosted copy. The original non-upload assets
keep their original hrefs. With `overwrite` set, assets a previous ARCO run
generated for the same conversions (the declared key or its numbered
variants, e.g. `parquet`, `parquet-2`) are replaced instead of accumulating
new suffixes on every re-run.

## Request

| Field | Meaning |
| --- | --- |
| `source_uri` | The STAC Item JSON — local path, `s3://` URI, or HTTP(S) URL. A link to a hosted Item is the typical input; there is no need to stage it on S3 first. |
| `item_target` | Prefix for the updated Item JSON; omit to rewrite the source Item in place (S3 only) or write the updated Item to `s3://<bucket>/<item_id>.json`. |
| `assets_target` | Prefix for converted outputs; defaults to `item_target`, else the source folder (S3) or bucket root. |
| `bucket` | Destination bucket for both outputs; defaults to `oidc-<username>` from the bearer token. |
| `item_id` | Optional id override; defaults to the Item's existing id. |
| `conversions` | Optional `{asset key or href substring: declared conversion key}` table. |
| `overwrite` | Default `false`; existing outputs cause HTTP 409. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | Request-scoped S3 credentials from [`POST /v1/auth`](../auth.md#response); forwarded to every conversion service. Optional — the service-level `S3_*` environment is used otherwise. |

The minimal payload is just the Item link — the bucket defaults to your
personal bucket and both outputs fall back to its root:

```json
{
  "source_uri": "https://geo.example.org/csw-items/my-dataset.json"
}
```

## Response

`item_id`, the updated `stac_item`, `source_uri`, the `target_uri` the Item
JSON was written to, one `conversions[]` entry per converted asset
(`source_asset_key`, `feature`, `asset_key`, `target_uri`, `layers` for
GeoParquet), `stage_timings_ms` per pipeline stage, and `status`.

## Errors

- `401` — bearer token missing/invalid. A valid token without a usable username (needed to derive the default bucket) fails with `422`.
- `404` — `source_uri` not found.
- `409` — an output exists and `overwrite` is false.
- `422` — not a STAC Item, no usable id, unknown conversion key, or an asset without `href`.
- `502` — the source Item could not be fetched over HTTP.

## Good to know

- A `.zip` mapped to `gdb-to-parquet` must contain exactly one file
  geodatabase — the converter stages and inspects the archive. A `.zip`
  mapped to `shp-to-parquet` must contain at least one `.shp` (with its
  sidecar files); every `.shp` becomes one GeoParquet output.
- The Item is not validated against the STAC schema here — run
  [`validate`](validate-stac.md) or [assemble](assemble-stac.md) before
  [publishing](publish-stac.md).
