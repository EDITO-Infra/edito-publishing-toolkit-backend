"""Public request/response models for the Zarr-to-STAC-Item feature."""

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.formats.zarr import DatasetSummary
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.utils.field_types import LocalOutput


class ZarrToStacItemRequest(S3ConnectionParameters):
    """One Zarr-to-STAC-Item conversion.

    The standard output is the caller's S3 storage: the item JSON — named
    after the STAC Item id, ``<item_id>.json`` — inside ``target`` within
    ``bucket`` (which defaults to the personal ``oidc-<username>`` bucket
    derived from the bearer token). ``local_output`` overrides S3 output with
    a directory on the EPT service filesystem. Connection parameters are
    optional: a pure local-to-local conversion needs none, and when omitted
    for an ``s3://`` source or S3 output the service falls back to its S3_*
    environment configuration.
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
    session_token: str | None = Field(  # type: ignore[assignment]
        default=None,
        min_length=1,
        description="Temporary S3 session token, if any.",
    )

    source_uri: str = Field(
        min_length=1,
        description=(
            "Zarr source: a local store directory, an `s3://bucket/prefix/` "
            "store root, or an HTTP(S) URL of a Zarr store (v2 or v3)."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "S3 destination prefix relative to `bucket` (no `s3://` scheme): "
            "the item JSON is written to `s3://<bucket>/<target>/<item_id>.json`. "
            "End the value in `.json` to set the file name yourself. Required "
            "unless `local_output` is set."
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
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Write the item JSON to this directory on the EPT service "
            "filesystem instead of S3 (`<local_output>/<item_id>.json`, or a "
            "`.json` path to set the file name yourself). Takes precedence "
            "over `target`/`bucket`."
        ),
    )
    collection_id: str | None = Field(
        default=None,
        description=(
            "ID of the collection to attach the item to. Used as-is — the "
            "collection does not have to exist in the EDITO STAC API yet (an "
            "unpublished id, e.g. your own, is attached untouched). Optional: "
            "when omitted, the item is built standalone and the response "
            "carries a `suggested_collection_id` instead — pass it (or your "
            "own collection's id) here to attach the item."
        ),
    )
    item_id: str | None = Field(
        default=None,
        description="STAC Item id. Defaults to the source store's directory name.",
    )
    overwrite: bool = Field(default=False, description="Replace an existing item at target_uri.")
    decode_cf: bool = Field(
        default=True,
        description="Apply CF decoding (mask_and_scale, datetimes) when reading the store.",
    )
    consolidated: bool | None = Field(
        default=None,
        description=(
            "Require consolidated Zarr metadata (true), bypass it (false), or "
            "auto-detect (null, default: use consolidated metadata when "
            "present, scan otherwise)."
        ),
    )
    bbox: list[float] | None = Field(
        default=None,
        description=(
            "Spatial-extent override: `[west, south, east, north]` (or six "
            "values including elevation). Use when the dataset has no CF "
            "lat/lon coordinates or `geospatial_*` attributes; otherwise the "
            "extent is derived from the source. Overrides any derived value."
        ),
    )
    start_datetime: datetime | None = Field(
        default=None,
        description=(
            "Temporal-extent override (ISO 8601). Use when the dataset has no "
            "time coordinate or `time_coverage_*` attributes; otherwise the "
            "extent is derived from the source. Required together with "
            "`end_datetime`; overrides any derived value."
        ),
    )
    end_datetime: datetime | None = Field(
        default=None,
        description=(
            "Temporal-extent override (ISO 8601). Required together with "
            "`start_datetime`; overrides any derived value."
        ),
    )
    extra_properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional top-level properties merged into the STAC Item.",
    )

    @field_validator("source_uri", "target", "bucket", "local_output", "collection_id")
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

    @field_validator("collection_id")
    @classmethod
    def validate_collection_id(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("collection_id must be a non-empty collection id when provided.")
        return value

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if len(value) not in (4, 6):
            raise ValueError("bbox must have 4 (2D) or 6 (3D) values.")
        if not all(math.isfinite(coordinate) for coordinate in value):
            raise ValueError("bbox values must be finite.")
        if value[0] > value[-2] or value[1] > value[-1]:
            raise ValueError(
                "bbox must be ordered [west, south, east, north]: "
                "west <= east and south <= north."
            )
        return value

    @model_validator(mode="after")
    def validate_output_choice(self) -> "ZarrToStacItemRequest":
        if bool(self.target) == bool(self.local_output):
            raise ValueError(
                "Exactly one output is required: `target` (S3 prefix within "
                "`bucket`) or `local_output` (a directory on the EPT filesystem)."
            )
        return self

    @model_validator(mode="after")
    def validate_temporal_pair(self) -> "ZarrToStacItemRequest":
        if (self.start_datetime is None) != (self.end_datetime is None):
            raise ValueError("start_datetime and end_datetime must be provided together.")
        if (
            self.start_datetime is not None
            and self.end_datetime is not None
            and self.start_datetime > self.end_datetime
        ):
            raise ValueError("start_datetime must not be after end_datetime.")
        return self


class DatasetSummaryModel(BaseModel):
    """Public view of the Zarr metadata the item was built from."""

    model_config = ConfigDict(extra="forbid")

    source: str
    dims: dict[str, int]
    coords: dict[str, dict[str, Any]]
    variables: dict[str, dict[str, Any]]
    attrs: dict[str, str]
    bbox: list[float] | None
    start_datetime: str | None
    end_datetime: str | None
    standard_names: list[str]

    @classmethod
    def from_infra(cls, summary: DatasetSummary) -> "DatasetSummaryModel":
        """Map the infrastructure extraction DTO onto this public model."""
        return cls.model_validate(summary.to_dict())


class ZarrToStacItemResponse(BaseModel):
    """The generated STAC Item plus where it was written and how its collection was resolved."""

    model_config = ConfigDict(extra="forbid")

    stac_item: dict[str, Any] = Field(description="The generated STAC Item JSON.")
    target_uri: str = Field(description="Location the item was written to (local path or s3:// URI).")
    collection_id: str | None = Field(
        description=(
            "Collection the item belongs to, or null when the item is standalone "
            "(no collection_id was provided — items are never attached to a "
            "collection automatically; see suggested_collection_id)."
        )
    )
    collection_source: str = Field(
        description=(
            "How the collection was resolved: `provided` (explicit collection_id, "
            "used as-is) or `standalone` (no collection attached — see "
            "suggested_collection_id)."
        )
    )
    suggested_collection_id: str | None = Field(
        description=(
            "Collection suggestion for standalone items: the id of an existing "
            "EDITO STAC collection whose text matches the dataset's variables or "
            "CF standard_names, or otherwise a kebab-case id derived from the "
            "first CF standard_name. Attach the item by re-running with this "
            "value as `collection_id` (for a collection you created) once chosen."
        )
    )
    dataset: DatasetSummaryModel = Field(description="Zarr metadata the item was built from.")
