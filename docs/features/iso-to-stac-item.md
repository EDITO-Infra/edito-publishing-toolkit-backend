# ISO19139 to STAC Item

Convert an ISO19139 metadata record into a STAC Item and write the Item JSON into your EDITO S3 storage.

## Endpoint

```text
POST /v1/metadata/iso-to-stac-item
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

ISO record from a CSW/HTTP endpoint, into your personal bucket (bucket omitted):

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/iso-to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://geo.example.org/csw?service=CSW&request=GetRecordById&id=my-dataset",
    "target": "my-metadata",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

The generated Item JSON is written to `s3://oidc-<username>/my-metadata/<item_id>.json` and also returned in the response body.

Output to another S3 bucket you can access (e.g. a project bucket `project-<projectid>`), from a local ISO record:

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/iso-to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "/data/metadata/my-dataset.xml",
    "bucket": "project-<projectid>",
    "target": "my-metadata",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

Override to a local output directory on the EPT service filesystem instead of S3 (no S3 connection parameters needed; note `"target": null`):

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/metadata/iso-to-stac-item" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_uri": "https://geo.example.org/csw?service=CSW&request=GetRecordById&id=my-dataset",
    "target": null,
    "local_output": "/workspace/my-metadata"
  }'
```

## Conversion

The record must contain a geographic bounding box and usable temporal information; conversion fails with a validation error otherwise. The mapping:

- **Geometry / bbox** — from `gmd:EX_GeographicBoundingBox` (polygon + 4-value bbox).
- **Datetime** — from the temporal extent (`start_datetime`/`end_datetime`) or, when only a single date exists (creation, publication, revision, …), from `properties.datetime`.
- **Title / description** — from the citation title and abstract.
- **Keywords** — from `gmd:descriptiveKeywords`.
- **Providers** — from `gmd:CI_ResponsibleParty` (organisation or individual plus role).
- **Constraints / licenses** — from `gmd:MD_LegalConstraints`; known SPDX ids (CC-BY-4.0, CC0-1.0, MIT, …) are matched from the constraint text.
- **Assets** — one asset per `gmd:CI_OnlineResource` in the distribution info. Asset type, title, media type, and roles are resolved through a translation table (`asset_config.yaml` in the feature; covers `.nc`, `.zarr`, `.tif`/`.tiff`, `.shp`, `.zip` and WMS/WFS/WMTS URL patterns). Override per request with `options.assets_config_path` (same YAML/JSON structure).
- **CSW metadata asset** — when `options.csw_endpoint` is set, a `csw_iso_metadata` asset pointing at a CSW `GetRecordById` request for the record is added.

## Request

| Field | Meaning |
| --- | --- |
| `source_uri` | The ISO19139 XML record — type is auto-detected: `http://`/`https://` URL, `s3://bucket/key` URI, or a path on the EPT service filesystem (the server, not the API caller's computer). |
| `target` | Destination folder or prefix inside the bucket, such as `my-metadata`. Do not include `s3://bucket` — EPT appends the file name `<item_id>.json` automatically. Required for the standard S3 output; pass `null` only when `local_output` is used instead. |
| `bucket` | Optional. Defaults to your personal EDITO bucket `oidc-<username>`, derived from your bearer token. Ignored when `local_output` is set. |
| `local_output` | Optional override: directory on the EPT service filesystem to write the Item JSON into instead of S3. When set, `target` must be `null`; `bucket` is ignored. |
| `item_id` | Optional override for the STAC Item id; defaults to the ISO `fileIdentifier`. Also names the output file. |
| `collection_id` | Optional. Only set if you already know the collection the Item belongs to; EPT does not resolve or validate it. |
| `overwrite` | Default `false`; an existing output file causes the operation to fail (HTTP 409). |
| `options` | Optional advanced mapping options: `csw_endpoint`, `csw_version`, `iso_schema`, `assets_config_path`, `stac_version`, include flags, and `extra_properties` merged into the Item properties. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | S3 connection parameters from the `s3_credentials` object returned by [`POST /v1/auth`](../auth.md#response). Optional: a pure local or http(s)-source to `local_output` conversion needs none; when omitted for S3 input or output, the service-level `S3_*` environment configuration is used. Credentials are request-scoped: they do not inherit an ambient AWS profile and are not installed into the EPT process environment. |

## Response

`item_id`, the generated `stac_item` object, the destination `bucket` (null for local output), the full `target_uri` of the written Item JSON, and `status` (`"completed"`).

## Errors

| Status | Cause |
| --- | --- |
| 400 | Source URL scheme is not allowed. |
| 404 | Source path does not exist. |
| 409 | Output file exists and `overwrite` is `false`. |
| 422 | Record has no usable bounding box or temporal information, malformed output options, or the source is not valid XML. |
| 502 | The source HTTP endpoint could not be reached. |
