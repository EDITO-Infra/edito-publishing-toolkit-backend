from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.utils.field_types import LocalOutput


class _OpenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GdbToParquetRequest(S3ConnectionParameters):
    """Conversion inputs for one local file geodatabase.

    The source is a ``.gdb`` directory, a ``.zip`` archive containing
    exactly one, or an HTTP(S) URL of such a ``.zip`` (staged to a temporary
    workspace before conversion). The standard output is the caller's S3
    storage: one GeoParquet file per layer — named after the layer — inside
    ``target`` within ``bucket`` (which defaults to the personal
    ``oidc-<username>`` bucket derived from the bearer token).
    ``local_output`` overrides S3 output with a directory on the EPT service
    filesystem. Sources are local paths or remote URLs, never ``s3://``.
    """

    endpoint_url: str | None = Field(  # type: ignore[assignment]
        default=None, description="HTTPS endpoint for S3-compatible storage (S3 output only)."
    )
    access_key_id: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, description="Temporary S3 access key ID (S3 output only)."
    )
    secret_access_key: str | None = Field(  # type: ignore[assignment]
        default=None, min_length=1, description="Temporary S3 secret access key (S3 output only)."
    )
    session_token: str | None = Field(
        default=None, min_length=1, description="Temporary S3 session token, if any."
    )
    source_uri: str = Field(
        min_length=1,
        description=(
            "Local .gdb directory, local .zip archive containing one, or HTTP(S) "
            "URL of such a .zip."
        ),
    )
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token; project buckets "
            "you can access are equally valid. Ignored when local_output is set."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "S3 destination prefix relative to `bucket` (no `s3://` scheme): one "
            "GeoParquet file per layer is written to "
            "`s3://<bucket>/<target>/<layer>.parquet`. Required unless "
            "`local_output` is set."
        ),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Directory on the EPT service filesystem to write the per-layer "
            "GeoParquet files into instead of S3. Takes precedence over "
            "`target`/`bucket`."
        ),
    )
    layers: list[str] | None = Field(
        default=None,
        description="Layer names to convert; defaults to all feature classes and tables.",
    )
    overwrite: bool = Field(
        default=False, description="Replace an existing target directory or S3 objects."
    )

    @field_validator("source_uri", "target", "bucket", "local_output")
    @classmethod
    def strip_uri(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value

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
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        target = value.strip().strip("/")
        if not target or target.lower().startswith("s3://"):
            raise ValueError(
                "target must be a prefix relative to bucket; do not include s3://bucket."
            )
        return target

    @field_validator("layers")
    @classmethod
    def validate_layers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        names = [name.strip() for name in value]
        if not names or any(not name for name in names):
            raise ValueError("layers must be a non-empty list of non-empty layer names.")
        return names

    @model_validator(mode="after")
    def validate_output_and_credentials(self) -> "GdbToParquetRequest":
        credentials = (self.endpoint_url, self.access_key_id, self.secret_access_key)
        if self.local_output:
            if self.target or self.bucket:
                raise ValueError(
                    "Use either S3 output (target, optionally bucket) or local_output — not both."
                )
            if any(credentials):
                raise ValueError("S3 connection parameters are only valid with S3 output.")
        elif not self.target:
            raise ValueError(
                "target is required: the prefix within your S3 bucket "
                "(or use local_output to write to the EPT filesystem instead)."
            )
        return self

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            # Mirrors validate_output_and_credentials: S3 output (target) or local_output.
            "anyOf": [{"required": ["target"]}, {"required": ["local_output"]}],
        },
    )


class GdbToParquetResponse(_OpenApiModel):
    """Result of converting a file geodatabase to GeoParquet."""

    source_uri: str
    target_uri: str
    layers: list[str] = Field(description="Layer names that were converted, in output order.")
    feature_counts: dict[str, int]
    crs: dict[str, str | None] = Field(
        description=(
            "Coordinate reference system label per layer (e.g. 'EPSG:31370'); null "
            "when the layer has no geometry or an unknown CRS."
        )
    )
    outputs: list[str] = Field(description="Written file paths, one per layer.")
    status: str
