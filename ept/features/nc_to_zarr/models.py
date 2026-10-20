from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.utils.field_types import LocalOutput


class _OpenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NetcdfToZarrRequest(S3ConnectionParameters):
    """NetCDF to Zarr conversion inputs.

    The standard output is the caller's S3 storage: the Zarr store — named
    after the source without its `.nc` extension, with a `.zarr` suffix —
    inside `target` within `bucket` (which defaults to the personal
    `oidc-<username>` bucket derived from the bearer token). `local_output`
    overrides S3 output with a directory on the EPT service filesystem.
    Connection parameters are optional: a pure local-to-local conversion needs
    none, and when omitted for S3 input or output the service falls back to
    its S3_* environment configuration.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            # Mirrors validate_output: S3 output (target) or local_output.
            "anyOf": [{"required": ["target"]}, {"required": ["local_output"]}],
        },
    )

    # Redeclared optional: S3 credentials are only needed when an S3 source or
    # target is actually used.
    endpoint_url: str | None = Field(  # type: ignore[assignment]
        default=None,
        description="HTTPS endpoint for EDITO S3-compatible storage, e.g. https://minio.dive.edito.eu.",
    )
    access_key_id: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=1,
        description="Temporary S3 access key ID (from POST /v1/auth s3_credentials).",
    )
    secret_access_key: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=1,
        description="Temporary S3 secret access key (from POST /v1/auth s3_credentials).",
    )

    source_uri: str = Field(
        min_length=1,
        description=(
            "NetCDF source: local path on the EPT service filesystem, s3:// URI, "
            "or HTTP(S) URL including OPeNDAP/DODS, GRIDDAP, and THREDDS endpoints."
        ),
    )
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token; other buckets you "
            "can access, e.g. project buckets `project-<projectid>`, are equally "
            "valid. Ignored when local_output is set."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "Destination folder or prefix within the bucket; the source name without "
            "its .nc extension (plus a .zarr suffix) is appended automatically unless "
            "output_name is provided, or the value ends in `.zarr` — then it is used "
            "as the store path. Required unless local_output is set."
        ),
    )
    output_name: str | None = Field(
        default=None,
        description=(
            "Store name for the output Zarr store; overrides the name derived from "
            "the source. A .zarr suffix is appended when missing."
        ),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Directory on the EPT service filesystem to write the Zarr store "
            "into instead of S3 (the store name is appended the same way). "
            "When set, bucket and target must be null."
        ),
    )
    engine: str | None = Field(default=None, description="Optional xarray backend engine.")
    chunks: int | dict[str, int] | Literal["auto"] | None = Field(
        default=None,
        description="Optional xarray chunk configuration. Using chunks requires a chunk manager such as Dask.",
    )
    decode_cf: bool | None = Field(default=None, description="Optional xarray CF decoding override.")
    mask_and_scale: bool | None = Field(default=None, description="Optional xarray mask-and-scale override.")
    consolidated: bool = Field(default=True, description="Write consolidated Zarr metadata.")
    overwrite: bool = Field(default=False, description="Replace the target store if it already exists.")
    zarr_format: Literal[2, 3] | None = Field(
        default=None,
        description="Optional Zarr format version. Defaults to v2 for S3 output.",
    )

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
            raise ValueError("target must be a store path relative to bucket; do not include s3://bucket.")
        return target

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
                "output_name must be a single store name without path separators."
            )
        return name

    @field_validator("local_output")
    @classmethod
    def validate_local_output(cls, value: str | None) -> str | None:
        if value is None:
            return None
        local_output = value.strip()
        if not local_output:
            raise ValueError("local_output must be a non-empty filesystem path.")
        return local_output

    @model_validator(mode="after")
    def validate_output(self) -> "NetcdfToZarrRequest":
        if self.local_output:
            if self.target or self.bucket:
                raise ValueError(
                    "Use either S3 output (target, optionally bucket) or local_output — not both."
                )
        elif not self.target:
            raise ValueError(
                "target is required: the store path within your S3 bucket "
                "(or use local_output to write to the EPT filesystem instead)."
            )
        return self


class NetcdfToZarrResponse(_OpenApiModel):
    source_uri: str
    bucket: str | None = Field(default=None, description="Destination bucket; null for local output.")
    target_uri: str
    status: str
