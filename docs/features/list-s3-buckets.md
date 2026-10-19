# List S3 buckets

List the S3 buckets the caller can see on EDITO's S3-compatible object storage, plus the personal bucket derived from the bearer token. Intended to run right after login so clients can offer the user's buckets as input, e.g. to prefill the `bucket` field of [Upload to S3](upload-s3.md).

## Endpoint

```text
POST /v1/storage/s3/buckets
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

```bash
curl --fail-with-body \
  -X POST "${EPT_API_URL}/v1/storage/s3/buckets" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint_url": "https://minio.dive.edito.eu",
    "access_key_id": "<s3_credentials.access_key_id from /v1/auth>",
    "secret_access_key": "<s3_credentials.secret_access_key from /v1/auth>",
    "session_token": "<s3_credentials.session_token from /v1/auth>"
  }'
```

## Request

| Field | Meaning |
| --- | --- |
| `endpoint_url`, `access_key_id`, `secret_access_key`, `session_token` | S3 connection parameters from the `s3_credentials` object returned by [`POST /v1/auth`](../auth.md#response). Credentials are request-scoped: they do not inherit an ambient AWS profile and are not installed into the EPT process environment. |

## Response

```json
{
  "endpoint_url": "https://minio.dive.edito.eu",
  "buckets": ["oidc-alice", "project-demo"],
  "default_bucket": "oidc-alice"
}
```

| Field | Meaning |
| --- | --- |
| `buckets` | Every bucket the supplied credentials can see, sorted alphabetically — your personal bucket plus any project buckets granted to your account. |
| `default_bucket` | Your personal EDITO bucket `oidc-<username>`, derived from the bearer token. This is the bucket used when `bucket` is omitted from upload requests. Null when the token carries no username claim. |

## Good to know

- Invalid, expired, or unauthorized credentials surface as a `502` problem (`s3-request-failed`), not an empty list.
- Bucket contents can be browsed at [https://datalab.dive.edito.eu/file-explorer](https://datalab.dive.edito.eu/file-explorer).
- See [S3 storage](../s3.md) for how credentials work and how the S3 endpoints are configured.
