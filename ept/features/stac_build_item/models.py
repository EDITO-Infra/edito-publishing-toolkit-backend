"""Public request/response models for the STAC Item builder.

Three input modes, combinable: individual fields, a complete object via
``json``, or an existing object fetched from ``source_url``. Explicit fields
override a base object; without a base the Item is built from the fields.
``target`` omitted → preview only; set → export to S3 (``bucket`` optional,
defaults to the caller's personal bucket) or, with ``local_output`` (a
directory on the EPT filesystem), to a local folder.
Schema hints: ``json_schema_extra`` mirrors the model validators below with
standard JSON Schema keywords (``anyOf``/``dependentRequired``) so schema-driven
frontend forms can mark conditionally-required fields; keep in sync.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from ept.infrastructure.storage.s3 import is_http_url
from ept.infrastructure.utils.urlguard import require_public_http_url
from ept.infrastructure.utils.field_types import LocalOutput


def _example(value: object) -> dict:
    return {"json_schema_extra": {"example": value}}


class BuildItemRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "anyOf": [
                {"required": ["id"]},
                {"required": ["stac_json"]},
                {"required": ["source_url"]},
            ],
            "dependentRequired": {
                "start_datetime": ["end_datetime"],
                "end_datetime": ["start_datetime"],
            },
        },
    )

    # ── base object (edit mode) ─────────────────────────────────────────────
    stac_json: dict | None = Field(
        default=None,
        description="Complete STAC Item used as the base object; provided fields override it.",
    )
    source_url: HttpUrl | None = Field(
        default=None,
        max_length=2048,
        description="Fetch the base STAC Item from this URL (SSRF-guarded); provided fields override it.",
    )

    @field_validator("source_url")
    @classmethod
    def _reject_unsafe_fetch_target(cls, value: HttpUrl | None) -> HttpUrl | None:
        """SSRF boundary: refuse non-http(s), credentialed, or private-target URLs."""
        if value is not None:
            require_public_http_url(str(value))
        return value

    @model_validator(mode="after")
    def _require_id_without_base(self) -> BuildItemRequest:
        """Fields-mode needs an id; edit mode inherits the base object's."""
        if self.id is None and self.stac_json is None and self.source_url is None:
            raise ValueError("id is required when building from fields (no stac_json/source_url base).")
        return self

    # ── common ──────────────────────────────────────────────────────────
    id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9._\-~]+$",
        description="STAC Item id. Required when building from fields; defaults to the base object's id in edit mode (stac_json/source_url).",
        **_example("observations-2025-01"),
    )
    stac_extensions: list[str] = Field(
        default_factory=list,
        description="Extension schema URLs.",
        **_example(["https://stac-extensions.github.io/eo/v1.1.0/schema.json"]),
    )
    title: str | None = Field(default=None, description="Item title (stored in properties).")
    description: str | None = Field(default=None, description="Item description (stored in properties).")

    # ── spatial / temporal ──────────────────────────────────────────────────
    geometry: dict | None = Field(
        default=None,
        description="GeoJSON geometry. Derived automatically from bbox when omitted.",
        **_example({"type": "Polygon", "coordinates": [[[2.5, 51.0], [4.5, 51.0], [4.5, 51.6], [2.5, 51.6], [2.5, 51.0]]]}),
    )
    bbox: list[float] | None = Field(
        default=None,
        description="Bounding box: 4 or 6 coordinates. Derived from geometry when omitted.",
        **_example([2.5, 51.0, 4.5, 51.6]),
    )
    datetime: str | None = Field(
        default=None,
        description="Acquisition instant (ISO 8601), or use start_datetime + end_datetime for an interval.",
        **_example("2025-01-15T10:30:00Z"),
    )
    start_datetime: str | None = Field(default=None, description="Interval start (ISO 8601); requires end_datetime.")
    end_datetime: str | None = Field(default=None, description="Interval end (ISO 8601); requires start_datetime.")
    properties: dict = Field(
        default_factory=dict,
        description="Additional STAC properties. Title/description/datetime fields win on conflict.",
        **_example({"platform": "sentinel-3a"}),
    )
    collection: str | None = Field(
        default=None,
        description="Id of the collection this item belongs to.",
        **_example("observations-2025"),
    )
    assets: dict[str, dict] = Field(
        default_factory=dict,
        description="Named assets; each value needs at least `href`.",
        **_example({"data": {"href": "s3://oidc-<username>/catalog-1/observations-2025-01.zarr", "type": "application/vnd+zarr", "roles": ["data"]}}),
    )

    # ── links (standard STAC links; hrefs may point at objects that do not exist yet) ──
    links: list[dict] = Field(
        default_factory=list,
        description=(
            'Standard STAC links, e.g. [{"rel": "root", "href": ...}, '
            '{"rel": "parent", "href": ...}]. Each entry needs `rel` and `href`.'
        ),
        **_example(
            [
                {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
                {"rel": "parent", "href": "s3://oidc-<username>/catalog-1/collection.json"},
            ]
        ),
    )

    @field_validator("links")
    @classmethod
    def _require_link_rel_and_href(cls, value: list[dict]) -> list[dict]:
        for link in value:
            if not link.get("rel") or not link.get("href"):
                raise ValueError("each link needs non-empty `rel` and `href`")
        return value

    # ── escape hatch ────────────────────────────────────────────────────────
    extra_fields: dict = Field(
        default_factory=dict,
        description="Arbitrary top-level fields merged into the built object last (e.g. extension fields).",
    )

    # ── export (omit `target` to preview only) ──────────────────────────────
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket name. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token. Other buckets your "
            "account can access — e.g. project buckets like `project-<projectid>` — "
            "are equally valid; browse them at "
            "https://datalab.dive.edito.eu/file-explorer. Must be a bucket name "
            "without s3:// or an object key."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "Destination folder: an S3 prefix within `bucket`. `item.json` is "
            "appended automatically, unless the value ends in `.json` — then "
            "it is used as the object's file name. Omit to preview only."
        ),
        **_example("catalog-1"),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Write to this directory on the EPT filesystem instead of S3 "
            "(`item.json` is appended, or a `.json` path to set the file name "
            "yourself). Takes precedence over `target`/`bucket`."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace an existing target.")

    # ── S3 connection (same flat shape as upload_s3; needed for `s3://` targets) ──
    endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint for `s3://` targets; falls back to the service's S3_* environment.",
    )
    access_key_id: str | None = Field(
        default=None, description="Temporary S3 access key ID for `s3://` targets."
    )
    secret_access_key: str | None = Field(
        default=None, description="Temporary S3 secret access key for `s3://` targets."
    )
    session_token: str | None = Field(
        default=None, description="Temporary S3 session token for `s3://` targets."
    )

    @field_validator("bucket")
    @classmethod
    def _validate_bucket(cls, value: str | None) -> str | None:
        if value is None:
            return None
        bucket = value.strip()
        if not bucket or bucket.lower().startswith("s3://") or "/" in bucket:
            raise ValueError("bucket must be a bucket name without s3:// or an object key.")
        return bucket

    @field_validator("target")
    @classmethod
    def _validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        target = value.strip()
        if not target:
            raise ValueError("target must include a destination folder or prefix.")
        if is_http_url(target):
            raise ValueError("target must be an S3 prefix, not an http(s) URL.")
        if target.lower().startswith("s3://"):
            raise ValueError(
                "target must be relative to the bucket; do not include s3://bucket — "
                "pass the bucket in the `bucket` field instead."
            )
        return target.rstrip("/")


class BuildItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stac_object: dict = Field(description="The built STAC Item as JSON.")
    warnings: list[str] = Field(
        default_factory=list,
        description="Structural findings that keep the object from being fully spec-compliant.",
    )
    exported: bool = Field(description="True when `target` was given and written.")
    target_uri: str | None = None
    status: str = Field(description="`preview` or `completed`.")
