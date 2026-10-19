"""Public request/response models for the ISO19139 → STAC Item feature."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.utils.field_types import LocalOutput

from .adapter import Iso19139ToStacOptions


class _OpenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IsoToStacItemRequest(S3ConnectionParameters):
    """ISO19139 → STAC Item conversion inputs.

    The standard output is the caller's S3 storage: the generated Item JSON —
    named after the STAC Item id, `<item_id>.json` — inside the required
    `target` within `bucket` (which defaults to the personal `oidc-<username>`
    bucket derived from the bearer token). `local_output` overrides S3 output
    with a directory on the EPT service filesystem; set `target` to null when
    using it. Connection parameters are optional: a pure local-to-local
    conversion needs none, and when omitted for an `s3://` source or S3
    output the service falls back to its S3_* environment configuration.
    """

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
            "ISO19139 XML record: local path on the EPT service filesystem, "
            "s3:// URI, or HTTP(S) URL."
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
        description=(
            "Destination folder or prefix within the bucket; the Item JSON "
            "name (`<item_id>.json`) is appended automatically, unless the "
            "value ends in `.json` — then it is used as the file name. "
            "Required for the standard S3 output — pass null only when "
            "local_output is used instead."
        ),
    )
    item_id: str | None = Field(
        default=None,
        description=(
            "Override the STAC Item id; defaults to the ISO fileIdentifier. "
            "Also names the output file `<item_id>.json`."
        ),
    )
    collection_id: str | None = Field(
        default=None,
        description=(
            "Optional collection the Item belongs to. Not resolved or "
            "validated; only set it if you already know the collection id."
        ),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Optional override: directory on the EPT service filesystem to "
            "write the Item JSON into instead of S3 (`<item_id>.json` is "
            "appended, or a `.json` path to set the file name yourself). When "
            "set, target must be null; bucket is ignored."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace the output file if it already exists.")
    options: Iso19139ToStacOptions = Field(
        default_factory=Iso19139ToStacOptions,
        description="Advanced ISO-to-STAC mapping options (CSW endpoint, asset config, extra properties).",
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
            raise ValueError("target must be an item path relative to bucket; do not include s3://bucket.")
        return target

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
    def validate_output(self) -> IsoToStacItemRequest:
        # target is required by the schema; it must be null exactly when the
        # local_output override is used, so exactly one output is always set.
        if self.local_output and self.target:
            raise ValueError(
                "Use either S3 output (target, optionally bucket) or local_output — not both."
            )
        return self

    def resolved_options(self) -> Iso19139ToStacOptions:
        """Options with the request-level collection override applied."""

        if self.collection_id:
            return self.options.model_copy(update={"default_collection": self.collection_id})
        return self.options


class IsoToStacItemResponse(_OpenApiModel):
    """Result of one ISO19139 → STAC Item conversion."""

    item_id: str = Field(description="Identifier of the generated STAC Item.")
    stac_item: dict[str, Any] = Field(description="The generated STAC Item JSON.")
    bucket: str | None = Field(default=None, description="Destination bucket; null for local output.")
    target_uri: str = Field(description="Full location of the written Item JSON.")
    status: str = Field(description='"completed" when the Item JSON was written.')
