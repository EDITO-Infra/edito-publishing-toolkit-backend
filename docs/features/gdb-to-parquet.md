# Convert GDB to GeoParquet

Convert an ESRI file geodatabase (`.gdb` directory, `.zip` archive containing exactly one, or an HTTP(S) URL of such a `.zip`) into GeoParquet — one file per layer, each with its own schema, CRS (as PROJJSON), and geometry types (GeoParquet 1.1.0, WKB encoding, ZSTD). Layers are read and written in chunks, so memory stays flat on multi-GB geodatabases. No reprojection. Output is S3 by default (one file per layer under an `s3://<bucket>/<target>/` prefix); `local_output` writes to a directory on the EPT service filesystem instead.

## Request

To S3, using your personal bucket (`oidc-<username>`, derived from your bearer token) and its default endpoint:

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/transform/gdb-to-parquet" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "/data/MSFD_package.gdb",
    "target": "geo/out"
  }'
```

To a specific bucket/project prefix, with request-scoped S3 credentials from POST /v1/auth:

```json
{
  "source_uri": "https://example.org/MSFD_package.zip",
  "bucket": "project-ept-projectsforum",
  "target": "arco-data/shipwrecks",
  "endpoint_url": "https://minio.dive.edito.eu",
  "access_key_id": "<s3_credentials.access_key_id>",
  "secret_access_key": "<s3_credentials.secret_access_key>",
  "session_token": "<s3_credentials.session_token>"
}
```

To a directory on the EPT service filesystem (mutually exclusive with `target`/`bucket` and S3 credentials):

```json
{
  "source_uri": "/data/MSFD_package.gdb",
  "local_output": "/data/out"
}
```

Select specific layers (defaults to all feature classes and tables):

```json
{
  "source_uri": "/data/pkg.zip",
  "local_output": "/data/out",
  "layers": ["points", "areas"],
  "overwrite": true
}
```

## Response

```json
{
  "source_uri": "/data/MSFD_package.gdb",
  "target_uri": "s3://oidc-<your-username>/geo/out",
  "layers": ["areas", "points"],
  "feature_counts": {"points": 2, "areas": 1},
  "crs": {"points": "EPSG:31370", "areas": "EPSG:31370"},
  "outputs": ["s3://oidc-<your-username>/geo/out/areas.parquet", "s3://oidc-<your-username>/geo/out/points.parquet"],
  "status": "completed"
}
```

`target_uri` and `outputs` appear only in the **response**; the request takes `target`/`bucket`/`local_output`.

## Notes

- Request validation is `extra="forbid"` — unknown fields (e.g. a request-level `target_uri`) fail with 422.
- `target` is a prefix relative to `bucket` and must not include `s3://` or the bucket name; `bucket` must be a bare bucket name. `bucket` defaults to your personal `oidc-<username>` bucket.
- When `endpoint_url` is provided, `target_uri`/`outputs` are full `https://` URLs; `s3://` URIs otherwise.
- Sources are local paths or HTTP(S) URLs, never `s3://`.
- The target must not exist (local) or must not collide with existing objects (S3) unless `overwrite` is true.
- Layer names are sanitized into file names (`[^A-Za-z0-9._-]` → `_`); collisions fail the request.
- Non-spatial tables convert too; their files carry no geometry column and `crs` is null.
- S3 credentials are request-scoped; they never enter the process environment.
- The optional GeoParquet `bbox` key is not written (streaming writer cannot amend the footer); spatial-filter pushdown degrades but files remain spec-valid.
