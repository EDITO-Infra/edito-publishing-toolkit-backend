from pydantic import BaseModel, ConfigDict, Field

from ept.infrastructure.services.edito_auth import S3ConnectionParameters


class ListS3BucketsRequest(S3ConnectionParameters):
    """S3 connection parameters used to list the caller's buckets.

    Pass the `s3_credentials` returned by POST /v1/auth: `endpoint_url`,
    `access_key_id`, `secret_access_key`, and `session_token`.
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
