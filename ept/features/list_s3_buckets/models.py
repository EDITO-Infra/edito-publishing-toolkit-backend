from pydantic import BaseModel, ConfigDict, Field

from ept.infrastructure.services.edito_auth import S3ConnectionParameters


class ListS3BucketsRequest(S3ConnectionParameters):
    """S3 connection parameters used to list the caller's buckets.

    Supplied as request headers on GET /v1/storage/s3/buckets — the
    `s3_credentials` returned by POST /v1/auth: `X-EDITO-S3-Endpoint-Url`,
    `X-EDITO-S3-Access-Key-Id`, `X-EDITO-S3-Secret-Access-Key`, and
    `X-EDITO-S3-Session-Token`.
    """


class ListS3BucketsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_url: str = Field(description="S3 endpoint the buckets were listed from.")
    buckets: list[str] = Field(
        description="Bucket names visible to the supplied credentials, sorted alphabetically.",
    )
    default_bucket: str | None = Field(
        default=None,
        description=(
            "Caller's personal EDITO bucket `oidc-<username>` derived from the bearer "
            "token; this is the bucket used when `bucket` is omitted from upload "
            "requests. Null when the token carries no username claim."
        ),
    )
