# List S3 buckets

List the S3 buckets the caller can see on EDITO's S3-compatible object storage, plus the personal bucket derived from the bearer token.

Password login ([POST /v1/auth](../auth.md)) registers your S3 session with the EPT instance, so after one login this endpoint needs only the bearer token — no `X-EDITO-S3-*` headers. Use it to discover your buckets before prefilling the `bucket` field of [Upload to S3](upload-s3.md).

## Endpoint

```text
GET /v1/storage/s3/buckets
```

Authentication: `Authorization: Bearer <access_token>`.

## Usage

After a password login, one call with the bearer token is enough:

```bash
curl --fail-with-body \
  -X GET "${EPT_API_URL}/v1/storage/s3/buckets" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-EDITO-S3-Endpoint-Url: https://minio.dive.edito.eu" \
  -H "X-EDITO-S3-Access-Key-Id: <s3_credentials.access_key_id from /v1/auth>" \
  -H "X-EDITO-S3-Secret-Access-Key: <s3_credentials.secret_access_key from /v1/auth>" \
  -H "X-EDITO-S3-Session-Token: <s3_credentials.session_token from /v1/auth>"
```

## Request

The S3 credential headers are optional. Supply either all mandatory ones or none:

- **Omitted** (recommended): EPT resolves the S3 session registered by your last password login. Returns a `409` problem (`s3-session-missing`) when none is registered — for example after a refresh-token grant or an EPT restart; log in again via the password grant to fix it.
- **Supplied**: the `X-EDITO-S3-*` headers override the registered session — `X-EDITO-S3-Endpoint-Url`, `X-EDITO-S3-Access-Key-Id`, `X-EDITO-S3-Secret-Access-Key`, and `X-EDITO-S3-Session-Token` from the `s3_credentials` object returned by [`POST /v1/auth`](../auth.md#response); the session-token header is optional (only needed for STS-issued temporary credentials). Credentials are request-scoped: they do not inherit an ambient AWS profile and are not installed into the EPT process environment.

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
