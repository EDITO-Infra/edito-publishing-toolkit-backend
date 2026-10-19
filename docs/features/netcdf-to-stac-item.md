# NetCDF metadata to STAC Item

Build a [STAC Item](https://stacspec.org) from the metadata of a NetCDF source and write it to S3 (default) or a local directory.

EPT opens the NetCDF (local file, `s3://` URI, or HTTP(S)/OPeNDAP URL), reads **dimensions, coordinates, data variables, and global/variable attributes**, derives the spatial extent (from CF latitude/longitude coordinates or `geospatial_*` attributes) and the temporal extent (from the time coordinate or `time_coverage_*` attributes), then builds and validates a STAC 1.1 Item whose single `data` asset points at the source.

## Endpoint

```text
POST /v1/metadata/netcdf/to-stac-item
```

Authentication: `Authorization: Bearer <access_token>`.

## Collection: optional, with a suggestion

STAC does not require an Item to belong to a collection — standalone items are valid and can be attached to a collection later (a planned `add_stac_item` feature will do exactly that). EPT resolves the owning collection in this order:

1. **Explicit** — if `collection_id` is set, it is attached to the item as-is (`collection_source: "provided"`). It does **not** have to exist in the EDITO STAC API yet — an unpublished id (e.g. your own, to be created or published later) is used untouched.
2. **Standalone** — otherwise the item is built without a collection (`collection_source: "standalone"`, `collection_id: null`) and the response carries a `suggested_collection_id` so you can create a collection and attach the item later. EPT suggests but never auto-attaches a matched collection.

The suggestion is a kebab-case slug of the first data-variable CF `standard_name` — an official vocabulary term from the [CF standard name table](https://cfconventions.org). For BODC-flavoured datasets the analogous terms are [P01 parameter codes](https://vocab.nerc.ac.uk/collection/P01/current/). Namespace it when creating the collection, e.g. `projects/<projectid>/<suggested_collection_id>`.

## Usage

Remote `.nc` file into your personal bucket (standalone item, with a suggested collection id). The item lands at `s3://<bucket>/<target>/<item_id>.json` — here `s3://oidc-<your-username>/stac/items/observations.json`:

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/netcdf/to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://erddap.example.org/erddap/files/datasetid/observations.nc",
    "target": "stac/items",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

Project bucket, file name chosen by the caller (any `target` ending in `.json` overrides the default `<item_id>.json`), explicit collection:

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/netcdf/to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://erddap.example.org/erddap/griddap/datasetid",
    "bucket": "project-<projectid>",
    "target": "stac/my-item.json",
    "collection_id": "projects/<projectid>/sea-surface-temperature"
  }'
```

Local output on the EPT service filesystem (overrides S3):

```json
{ "source_uri": "...", "local_output": "/workspace/stac" }
```

## Request

| Field | Meaning |
| --- | --- |
| `source_uri` | NetCDF source: local path on the EPT filesystem, `s3://bucket/key`, or HTTP(S) URL (plain `.nc` file or OPeNDAP/DAP endpoint). |
| `target` | S3 destination prefix relative to `bucket` (no `s3://` scheme): the item JSON is written to `s3://<bucket>/<target>/<item_id>.json`. Ending the value in `.json` overrides the file name. Required unless `local_output` is set. |
| `bucket` | Destination S3 bucket. Defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token; project buckets are equally valid. Ignored when `local_output` is set. |
| `local_output` | Directory on the EPT service filesystem instead of S3 (`<local_output>/<item_id>.json`, or a `.json` path to set the file name yourself). Takes precedence over `target`/`bucket`. |
| `collection_id` | Optional. Collection id attached to the item as-is — it does not have to exist in the EDITO STAC API. When omitted (and nothing matches), the item is standalone and `suggested_collection_id` carries a vocabulary-based suggestion. |
| `item_id` | Optional STAC Item id; defaults to the source file name without extension. Also names the output file (`<item_id>.json`). |
| `overwrite` | Default `false`; an existing item at the resolved target fails the request with 409. |
| `decode_cf` | Default `true`; applies CF decoding (mask/scale, datetimes) when reading. |
| `bbox`, `start_datetime`, `end_datetime` | Optional spatial/temporal overrides for datasets without CF lat/lon coordinates or `geospatial_*`/`time_coverage_*` attributes; `start_datetime` and `end_datetime` must be provided together. |
| `extra_properties` | Additional top-level properties merged into the item. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | S3 connection parameters from `POST /v1/auth`; required for `s3://` sources, optional otherwise (service-level S3_* environment fallback). Request-scoped, never installed into the EPT environment. |

## Response

```json
{
  "stac_item": {
    "type": "Feature",
    "stac_version": "1.1.0",
    "id": "observations",
    "bbox": [2.0, 50.0, 4.0, 52.0],
    "geometry": {"type": "Polygon", "coordinates": ["..."]},
    "properties": {
      "datetime": null,
      "start_datetime": "2025-01-01T00:00:00Z",
      "end_datetime": "2025-01-02T00:00:00Z",
      "Conventions": "CF-1.10",
      "variables": {"thetao": {"standard_name": "sea_water_potential_temperature", "units": "degree_Celsius"}}
    },
    "assets": {"data": {"href": "https://erddap.example.org/...", "roles": ["data"], "type": "application/x-netcdf"}}
  },
  "target_uri": "s3://oidc-<your-username>/stac/items/observations.json",
  "collection_id": null,
  "collection_source": "standalone",
  "suggested_collection_id": "sea-water-potential-temperature",
  "dataset": {
    "source": "https://erddap.example.org/...",
    "dims": {"time": 2, "lat": 2, "lon": 2},
    "coords": {"time": {}, "lat": {}, "lon": {}},
    "variables": {"thetao": {"standard_name": "sea_water_potential_temperature"}},
    "attrs": {"Conventions": "CF-1.10"},
    "bbox": [2.0, 50.0, 4.0, 52.0],
    "start_datetime": "2025-01-01T00:00:00",
    "end_datetime": "2025-01-02T00:00:00",
    "standard_names": ["sea_water_potential_temperature"]
  }
}
```

The `dataset` object is the full metadata extraction the item was built from; `collection_source` records whether the collection was `provided` (used as-is) or `standalone`.

## Good to know

- Items declare `datetime: null` with `start_datetime`/`end_datetime` carrying the temporal range (the STAC-recommended form for ranges).
- Standalone items declare no `collection` property — valid STAC, attachable later. Items with a `collection` also carry a `rel="collection"` link (required by the STAC 1.1 schema).
- Non-SPDX CF `license` text is kept in `dataset.attrs` but not promoted into the item's `license` property (STAC 1.1 requires an SPDX identifier there).
- HTTP `.nc` sources that cannot be opened with random access (e.g. netCDF-3 classic) are streamed to a temporary file on the EPT filesystem and opened locally; OPeNDAP/DAP endpoints are delegated to the `netcdf4` engine directly.
