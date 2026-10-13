# Upload to S3

Upload a remote HTTP(S) resource, or a file or directory on the EPT service filesystem, into EDITO's S3-compatible object storage.

## Endpoint

```text
POST /v1/storage/s3/upload
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

Remote file (standard — no staging needed), into your personal bucket (bucket omitted):

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/storage/s3/upload" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "https://data.example.org/input.nc",
    "target": "my-uploads",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

Local file already on the EPT machine, into an explicit bucket for 'projectid':

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/storage/s3/upload" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "source": "/data/input.nc",
    "bucket": "project-<projectid>",
    "target": "my-uploads",
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

## Request

| Field | Meaning |
| --- | --- |
| `source` | The resource to upload — type is auto-detected: an `http://` or `https://` URL is streamed from the remote host; anything else is a file or directory on the EPT service filesystem (the server, not the API caller's computer; no multipart file content). |
| `bucket` | Optional. Defaults to your personal EDITO bucket `oidc-<username>`, derived from your bearer token. Project buckets you can access (typically `project-<projectid>`) are equally valid. |
| `target` | Destination folder or prefix inside the bucket, such as `my-uploads`. Do not include `s3://bucket` or the source filename — EPT appends the filename from the source automatically, producing `s3://<bucket>/my-uploads/input.nc` in the examples. For a local directory, EPT appends the directory name and uploads its files below it. |
| `recursive` | Default `true`. Required for local directories. |
| `overwrite` | Default `false`; existing objects cause the operation to fail. |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | S3 connection parameters from the `s3_credentials` object returned by [`POST /v1/auth`](../auth.md#response). The STS `expiration` is response metadata, not a connection parameter. Credentials are request-scoped: they do not inherit an ambient AWS profile and are not installed into the EPT process environment. |

## Response

```json
{
  "bucket": "oidc-alice",
  "source": "https://data.example.org/input.nc",
  "target_uri": "s3://oidc-alice/my-uploads/input.nc",
  "uploaded": [
    "s3://oidc-alice/my-uploads/input.nc"
  ],
  "status": "completed"
}
```

The `bucket` field confirms where the upload landed (defaulted or explicit), and `uploaded` contains every written object URI.

## Good to know

- Browse your buckets and their contents at [https://datalab.dive.edito.eu/file-explorer](https://datalab.dive.edito.eu/file-explorer).
- See [S3 storage](../s3.md) for how credentials work and how the S3 endpoints are configured.
