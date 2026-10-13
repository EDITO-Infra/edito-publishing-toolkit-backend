from pydantic import BaseModel, ConfigDict, Field, field_validator

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import is_http_url


class _OpenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadS3Request(S3ConnectionParameters):
    """Connection and location inputs for one S3 upload.

    `source` is auto-detected: an http(s) URL is streamed from the remote
    host; anything else is treated as a file or directory on the EPT service
    filesystem.
    """
    source: str = Field(
        min_length=1,
        description=(
            "Resource to upload: an http:// or https:// URL (streamed into S3), "
            "or a file or directory path on the EPT service filesystem. The type "
            "is auto-detected from the value."
        ),
    )
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket name. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token. Other buckets your "
            "account can access — e.g. project buckets like `oidc-<projectid>` — "
            "are equally valid; browse them at "
            "https://datalab.dive.edito.eu/file-explorer. Must be a bucket name "
            "without s3:// or an object key."
        ),
    )
    target: str = Field(
        min_length=1,
        description=(
            "Destination folder or prefix within the bucket; the source name is appended "
            "automatically unless output_name is provided."
        ),
    )
    output_name: str | None = Field(
        default=None,
        description=(
            "File or folder name for the uploaded object; use to override the name "
            "derived from the source (e.g. for URLs ending in a generic segment "
            "like /formatters/xml)."
        ),
    )
    recursive: bool = Field(default=True, description="Upload local directory contents recursively.")
    overwrite: bool = Field(default=False, description="Replace existing S3 objects.")

    @field_validator("output_name")
    @classmethod
    def validate_output_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip().strip("/")
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or name.lower().startswith("s3://")
        ):
            raise ValueError(
                "output_name must be a single file or folder name without path separators."
            )
        return name

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str | None) -> str | None:
        if value is None:
            return None
        bucket = value.strip()
        if not bucket or bucket.lower().startswith("s3://") or "/" in bucket:
            raise ValueError("bucket must be a bucket name without s3:// or an object key.")
        return bucket

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        if value.strip().lower().startswith("s3://"):
            raise ValueError("target must be relative to bucket; do not include s3://bucket.")
        target = value.strip().strip("/")
        if not target:
            raise ValueError("target must include a destination folder or prefix within bucket.")
        return target

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        source = value.strip()
        if not source:
            raise ValueError(
                "source is required: an http:// or https:// URL, or a filesystem path."
            )
        if "://" in source and not is_http_url(source):
            raise ValueError(
                "source must be an http:// or https:// URL, or a filesystem path "
                "without a scheme."
            )
        return source


class UploadS3Response(_OpenApiModel):
    bucket: str = Field(description="Destination bucket that received the upload.")
    source: str
    target_uri: str
    uploaded: list[str]
    status: str
