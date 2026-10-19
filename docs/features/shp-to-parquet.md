# Convert Shapefile to GeoParquet

Convert ESRI shapefiles (a `.shp` file, a `.zip` archive containing one or more, or an HTTP(S) URL of such a `.zip`) into GeoParquet — one file per `.shp`, each with its own schema, CRS (as PROJJSON), and geometry types (GeoParquet 1.1.0, WKB encoding, ZSTD). Files are read and written in chunks, so memory stays flat on large shapefiles. No reprojection. Target is S3 by default (one file per shapefile under an `s3://<bucket>/<target>/` prefix); `local_output` writes to a directory on the EPT service filesystem instead.

A bare remote `.shp` URL is rejected: a shapefile needs its sidecar files (`.shx`, `.dbf`, `.prj`), so remote sources must be `.zip` archives.

## Request

To S3, using your personal bucket (`oidc-<username>`, derived from your bearer token) and its default endpoint:

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/transform/shp-to-parquet" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "/data/mangrove_eov_poly_2025.zip",
    "target": "arco-data/mangrove"
  }'
```

To a specific bucket/project prefix, with request-scoped S3 credentials from POST /v1/auth:

```json
{
  "source_uri": "https://files.example.org/data/mangrove_eov_poly_2025.zip",
  "bucket": "project-<projectid>",
  "target": "arco-data/mangrove",
  "endpoint_url": "https://minio.dive.edito.eu",
  "access_key_id": "<s3_credentials.access_key_id>",
  "secret_access_key": "<s3_credentials.secret_access_key>",
  "session_token": "<s3_credentials.session_token>"
}
```

To a directory on the EPT service filesystem (mutually exclusive with `target`/`bucket` and S3 credentials):

```json
{
  "source_uri": "/data/mangrove_eov_poly_2025.zip",
  "local_output": "/data/out"
}
```

Convert only specific shapefiles (by stem; defaults to every `.shp` found):

```json
{
  "source_uri": "/data/pkg.zip",
  "local_output": "/data/out",
  "layers": ["mangrove_eov_poly"],
  "overwrite": true
}
```

## Response

```json
{
  "source_uri": "/data/mangrove_eov_poly_2025.zip",
  "target_uri": "s3://oidc-<your-username>/arco-data/mangrove",
  "layers": ["mangrove_eov_poly"],
  "feature_counts": {"mangrove_eov_poly": 15432},
  "crs": {"mangrove_eov_poly": "EPSG:4326"},
  "outputs": ["s3://oidc-<your-username>/arco-data/mangrove/mangrove_eov_poly.parquet"],
  "status": "completed"
}
```

`target_uri` and `outputs` appear only in the **response**; the request takes `target`/`bucket`/`local_output`. When `endpoint_url` is provided, `target_uri`/`outputs` are full `https://` URLs; `s3://` URIs otherwise.

## Notes

- Request validation is `extra="forbid"` — unknown fields fail with 422.
- `target` is a prefix relative to `bucket` and must not include `s3://` or the bucket name; `bucket` must be a bare bucket name. `bucket` defaults to your personal `oidc-<username>` bucket.
- Sources are local paths or HTTP(S) URLs, never `s3://`.
- The target must not exist (local) or must not collide with existing objects (S3) unless `overwrite` is true.
- Shapefile stems are sanitized into file names (`[^A-Za-z0-9._-]` → `_`); collisions fail the request.
- Shapefiles without a usable `.prj` convert with `crs: null`; no reprojection is attempted.
- S3 credentials are request-scoped; they never enter the process environment.
