# Transform NetCDF to Zarr

Convert a NetCDF dataset into a Zarr store in your EDITO S3 storage, or — as an
override — on the EPT service filesystem.

`POST /v1/transform/nc-to-zarr` (Bearer auth required).

## Standard usage: remote NetCDF to your S3 storage

The easiest call names only a source and a target folder. The Zarr store is
named after the source file (without its `.nc` extension) and written into
`target` inside your personal EDITO bucket `oidc-<username>` (derived from
your bearer token):

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/transform/nc-to-zarr" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://data.example.org/datasets/observations-2025.nc",
    "target": "datasets"
  }'
```

This writes the store to `s3://oidc-<your-username>/datasets/observations-2025.zarr`.

Supply the S3 connection fields returned by `POST /v1/auth` as flat request
fields (`endpoint_url`, `access_key_id`, `secret_access_key`, optional
`session_token`). When they are omitted, the EPT service falls back to its own
`S3_*` environment configuration.

## Overrides

- **`bucket`** — write into a different bucket you can access (e.g. a project
  bucket `project-<projectid>`; browse your buckets at
  https://datalab.dive.edito.eu/file-explorer). Defaults to your personal
  `oidc-<username>` bucket.
- **`output_name`** — store name override; defaults to the source name without
  its `.nc`/`.nc4`/`.cdf` extension, with a `.zarr` suffix (appended when
  missing).
- **`local_output`** — directory on the EPT service filesystem to write the
  Zarr store into instead of S3 (the store name is appended the same way).
  When set, `bucket` and `target` must be null, and no S3 connection fields
  are needed for a purely local conversion.
- **`source_uri`** — local path on the EPT service filesystem, `s3://` URI, or
  HTTP(S) URL including OPeNDAP/DODS, GRIDDAP, and THREDDS endpoints. Remote
  HTTP(S) and S3 files are downloaded once into a local cache before opening
  (set `EPT_NC_CACHE` to change the cache directory).
- **`engine`** — xarray backend engine override.
- **`chunks`** — xarray chunk configuration (int, per-dimension dict, or
  `"auto"`); requires a chunk manager such as Dask.
- **`decode_cf`, `mask_and_scale`** — CF decoding overrides passed to
  `xarray.open_dataset`.
- **`consolidated`** (default `true`) — write consolidated Zarr metadata.
- **`overwrite`** (default `false`) — replace an existing target store. For S3
  targets this deletes all existing objects under the target prefix first.
- **`zarr_format`** — `2` or `3`; defaults to v2 for S3 output.

## Response

```json
{
  "source_uri": "https://erddap.example.org/erddap/griddap/observations-2025",
  "bucket": "oidc-myusername",
  "target_uri": "s3://oidc-myusername/datasets/observations-2025.zarr",
  "status": "completed"
}
```

`bucket` is `null` for local output. `target_uri` is a full `https://` URL
when `endpoint_url` is provided, an `s3://` URI otherwise. The operation
completes synchronously; when `overwrite` is `false` an existing target
store is rejected.
