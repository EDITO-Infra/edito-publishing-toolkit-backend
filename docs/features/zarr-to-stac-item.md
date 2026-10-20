# Zarr metadata to STAC Item

Build a [STAC Item](https://stacspec.org) from the metadata of a Zarr store and write it to S3 (default) or a local directory.

EPT opens the Zarr store (local directory, `s3://` prefix, or HTTP(S) URL), reads **dimensions, coordinates, data variables, and global/variable attributes**, derives the spatial extent (from CF latitude/longitude coordinates or `geospatial_*` attributes) and the temporal extent (from the time coordinate or `time_coverage_*` attributes), then builds and validates a STAC 1.1 Item whose single `data` asset points at the store.

## Endpoint

```text
POST /v1/metadata/zarr/to-stac-item
```

Authentication: `Authorization: Bearer <access_token>`.

## Collection: optional, with a suggestion

Pass `collection_id` to attach the item explicitly (`collection_source: "provided"`), or leave it out: the item is built standalone (`collection_source: "standalone"`, `collection_id: null`) and the response carries a `suggested_collection_id` — a kebab-case slug of the first data-variable CF `standard_name` from the [CF standard name table](https://cfconventions.org). EPT suggests but never auto-attaches a matched collection.

## Usage

Zarr store in your personal bucket, item written to `stac/items` (the bucket defaults to your personal `oidc-<your-username>` bucket, so the item lands at `s3://oidc-<your-username>/stac/items/observations.json`):

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/zarr/to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "s3://oidc-<your-username>/stores/observations.zarr",
    "target": "stac/items",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

## Request

| Field | Meaning |
| --- | --- |
| `source_uri` | Zarr store source: local directory, `s3://bucket/prefix`, or HTTP(S) URL. |
| `target` | S3 destination prefix relative to `bucket` (no `s3://` scheme): the item JSON is written to `s3://<bucket>/<target>/<item_id>.json`. Ending the value in `.json` overrides the file name. Required unless `local_output` is set. |
| `bucket` | Destination S3 bucket. Defaults to your personal EDITO bucket `oidc-<username>` derived from your bearer token; project buckets are equally valid. Ignored when `local_output` is set. |
| `local_output` | Directory on the EPT service filesystem instead of S3 (`<local_output>/<item_id>.json`, or a `.json` path to set the file name yourself). Takes precedence over `target`/`bucket`. |
| `collection_id` | Optional. Collection id attached to the item as-is — it does not have to exist in the EDITO STAC API. When omitted (and nothing matches), the item is standalone and `suggested_collection_id` carries a vocabulary-based suggestion. |
| `item_id` | Optional STAC Item id. When omitted, one is proposed: the dataset's own `id`, `identifier`, or `title` attribute (else the store directory name without the `.zarr` suffix), namespaced with your username — `<username>-<identifier>`. Also names the output file (`<item_id>.json`). |
| `overwrite` | Default `false`; an existing item at the resolved target fails the request with 409. |
| `decode_cf` | Default `true`; applies CF decoding (mask/scale, datetimes) when reading. |
| `consolidated` | Optional; whether to require consolidated Zarr metadata. By default xarray auto-detects. |
| `bbox`, `start_datetime`, `end_datetime` | Optional spatial/temporal overrides for stores without CF coordinates or attributes; `start_datetime` and `end_datetime` must be provided together. |
| `extra_properties` | Additional top-level properties merged into the item. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | S3 connection parameters from `POST /v1/auth`; required for `s3://` sources, optional otherwise (service-level S3_* environment fallback). Request-scoped, never installed into the EPT environment. |

## Response

Same shape as the [NetCDF variant](netcdf-to-stac-item.md#response): the validated `stac_item`, the `target_uri` it was written to, the resolved `collection_id` with `collection_source` (`provided` or `standalone`), a `suggested_collection_id`, and the full `dataset` metadata extraction the item was built from.

## Good to know

- The item's `data` asset is typed `application/x-zarr`.
- Items declare `datetime` (single instant from date attributes such as `date_created`) or `datetime: null` with `start_datetime`/`end_datetime` carrying the temporal range.
- Standalone items declare no `collection` property — valid STAC, attachable later. Items with a `collection` also carry a `rel="collection"` link (required by the STAC 1.1 schema).
- Zarr v2 and v3 stores are both auto-detected by zarr-python.
