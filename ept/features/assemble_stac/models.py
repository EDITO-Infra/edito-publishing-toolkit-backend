"""Public models for the STAC assemble feature."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.formats.stac import StacObjectType
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.utils.field_types import LocalOutput

__all__ = ["AssembleStacObject", "AssembleStacRequest", "AssembleStacResponse"]


class AssembleStacRequest(S3ConnectionParameters):
    """Assemble loose STAC JSON files into one valid, linked STAC tree.

    The source is a local directory, a local or remote ``.zip`` archive, or an
    ``s3://`` URI of a ``.zip`` object or a folder prefix. Every ``*.json``
    file is read and classified (catalog, collection, item); the tree
    structure is resolved from the files' own links (``parent``, ``child``,
    ``item``, ``collection``), an item's ``collection`` property, and the
    folder layout as fallback.

    Fields the items can provide are derived when missing: collection extents
    from their items' bbox/datetime, item bboxes from geometry, ids and
    ``stac_version``. Every exported object is schema-validated (issues are
    reported per object, never fatal).

    The standard output is the caller's S3 storage: the assembled tree is
    exported under ``s3://<bucket>/<target>`` — ``bucket`` defaults to the
    personal ``oidc-<username>`` bucket derived from the bearer token.
    ``local_output`` overrides S3 output with a directory on the EPT service
    filesystem. The S3 connection parameters serve an ``s3://`` source and an
    S3 output; for local-to-local conversions they must be omitted, and when
    omitted for S3 the service falls back to its S3_* environment
    configuration.
    """

    # Optional on this feature: only needed for S3 sources/output, unlike
    # auth-issued credentials where every field is always present.
    endpoint_url: str | None = Field(  # type: ignore[assignment]
        default=None,
        description="HTTPS endpoint for S3-compatible storage (S3 sources/output only).",
    )
    access_key_id: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=1,
        description="Temporary S3 access key ID (S3 sources/output only).",
    )
    secret_access_key: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=1,
        description="Temporary S3 secret access key (S3 sources/output only).",
    )
    session_token: str | None = Field(
        default=None,
        description="Temporary S3 session token (S3 sources/output only).",
    )
    source_uri: str = Field(
        min_length=1,
        description=(
            "Local directory of STAC JSON files, local or remote .zip archive, "
            "or s3:// URI of a .zip object or a folder prefix."
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
            "S3 destination prefix relative to `bucket` (no `s3://` scheme): the "
            "assembled STAC tree is exported under `s3://<bucket>/<target>` — "
            "catalog.json/collection.json/item files inside, linked relatively. "
            "Required unless `local_output` is set."
        ),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Directory on the EPT service filesystem to export the assembled "
            "tree into instead of S3. Takes precedence over `target`/`bucket`."
        ),
    )
    overwrite: bool = Field(True, description="Overwrite existing files or S3 objects")

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

    @model_validator(mode="after")
    def validate_output_and_credentials(self) -> AssembleStacRequest:
        is_s3_output = not self.local_output
        is_s3_source = self.source_uri.strip().lower().startswith("s3://")
        credentials = (
            self.endpoint_url,
            self.access_key_id,
            self.secret_access_key,
        )
        if self.local_output:
            if self.target or self.bucket:
                raise ValueError(
                    "Use either S3 output (target, optionally bucket) or local_output — not both."
                )
        elif not self.target:
            raise ValueError(
                "target is required: the prefix within your S3 bucket "
                "(or use local_output to write to the EPT filesystem instead)."
            )
        if not (is_s3_output or is_s3_source) and any(credentials):
            raise ValueError(
                "S3 connection parameters are only valid with an s3:// source_uri or S3 output."
            )
        return self

    model_config = {"extra": "forbid"}


class AssembleStacObject(BaseModel):
    """One file written to the workspace, with validation outcome."""

    path: str
    object_type: StacObjectType
    object_id: str | None = None
    validation_issues: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class AssembleStacResponse(BaseModel):
    """Result of a STAC assemble: what was assembled, written, and how it validated."""

    source_uri: str
    target_uri: str = Field(description="Full location the tree was exported to (s3:// URI or local path).")
    root_path: str = Field(..., description="Path of the root object, relative to target_uri")
    objects: list[AssembleStacObject]
    uploaded: list[str] = Field(
        default_factory=list,
        description="S3 URIs written (empty for a local output)",
    )
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
