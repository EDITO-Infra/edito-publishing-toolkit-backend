"""Public models for the STAC collection-suggestion feature."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.storage.s3 import is_http_url
from ept.infrastructure.utils.field_types import LocalOutput

__all__ = ["SuggestCollectionRequest", "SuggestCollectionResponse"]


class SuggestCollectionRequest(BaseModel):
    """Inputs for suggesting a STAC Collection from items and/or a base collection.

    Item sources (combinable, at least one required with a base): inline
    ``items``, ``item_links`` to fetch (http(s) URLs, ``s3://`` URIs, or EPT
    filesystem paths), or ``source_folder`` — a local directory or ``s3://``
    prefix whose ``*.json`` files are the items. A ``source_folder`` is
    mutually exclusive with ``items``/``item_links`` and always exports:
    ``collection.json`` is created inside the folder and the item files are
    re-linked in place.

    Without an export target the result is pure metadata (the suggested JSON
    feeds the STAC Collection builder form, which owns export targets). With
    ``target`` the items are fetched, a complete collection tree is exported
    there (``collection.json`` plus one file per item) — an S3 prefix in
    ``bucket`` (defaulting to the caller's personal bucket) or, with
    ``local_output``, a directory on the EPT filesystem.

    Fields the items provide are derived when missing: extent (union of
    bboxes/datetimes), license via SPDX matching, keywords, id.
    """

    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "STAC Items to derive the extent from (spatial union of bboxes, "
            "temporal union of datetimes). Optional when `base` is given; the "
            "base extent is kept as-is when no items are provided."
        ),
    )
    item_links: list[str] = Field(
        default_factory=list,
        description=(
            "STAC Item JSON locations to fetch and derive from — http(s) URLs "
            "(SSRF-guarded), s3:// URIs, or paths on the EPT service "
            "filesystem. Unreadable links are reported as warnings and "
            "skipped. Combined with any inline `items`."
        ),
    )
    source_folder: str | None = Field(
        default=None,
        description=(
            "Local directory or s3://bucket/prefix whose *.json files are the "
            "collection's items. Mutually exclusive with items/item_links. "
            "Always exports: collection.json is written inside the folder and "
            "the item files are re-linked in place (collection id, root/parent/"
            "collection links)."
        ),
    )
    base: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Existing STAC Collection to copy metadata from (title, description, "
            "license, providers). Its id's last segment prefixes the proposed "
            "id and its extent is recomputed when `items` are given."
        ),
    )
    id: str | None = Field(
        default=None,
        description=(
            "Explicit collection id (no '/' — ids are flat; the EDITO "
            "infrastructure adds any path at publish time). When omitted, one "
            "is proposed: `<base id>-<slug>` with a base, else "
            "`<username>-<slug>` derived from your bearer token."
        ),
    )
    title: str | None = Field(default=None, description="Human-readable title; also seeds the id slug.")
    description: str | None = Field(default=None, description="Collection description.")
    license: str | None = Field(
        default=None,
        description="SPDX license identifier; defaults to the base's, the items' shared license, or 'other'.",
    )

    # ── export (omit `target`, and no source_folder, to preview only) ────────
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket name. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token. Other buckets your "
            "account can access — e.g. project buckets like `project-<projectid>` — "
            "are equally valid. Must be a bucket name without s3:// or an object "
            "key. Only valid with `target`."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "Destination folder: an S3 prefix within `bucket`. `collection.json` "
            "plus one file per item is written inside it. Omit to preview only."
        ),
    )
    local_output: LocalOutput = Field(
        default=None,
        description=(
            "Write to this directory on the EPT filesystem instead of S3. Takes "
            "precedence over `target`/`bucket`."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "Replace an existing collection.json. With source_folder, item files "
            "are always re-linked in place; `overwrite` only guards collection.json."
        ),
    )

    # ── S3 connection (same flat shape as upload_s3; needed for s3:// sources/targets) ──
    endpoint_url: str | None = Field(
        default=None,
        description="S3 endpoint for s3:// sources/targets; falls back to the service's S3_* environment.",
    )
    access_key_id: str | None = Field(
        default=None, description="Temporary S3 access key ID for s3:// sources/targets."
    )
    secret_access_key: str | None = Field(
        default=None, description="Temporary S3 secret access key for s3:// sources/targets."
    )
    session_token: str | None = Field(
        default=None, description="Temporary S3 session token for s3:// sources/targets."
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            # Mirrors validate_sources_and_export: at least one source, and the
            # S3 connection triple is all-or-none.
            "anyOf": [
                {"required": ["items"]},
                {"required": ["item_links"]},
                {"required": ["source_folder"]},
                {"required": ["base"]},
            ],
            "dependentRequired": {
                "endpoint_url": ["access_key_id", "secret_access_key"],
                "access_key_id": ["endpoint_url", "secret_access_key"],
                "secret_access_key": ["endpoint_url", "access_key_id"],
            },
        },
    )

    @field_validator("items")
    @classmethod
    def validate_items(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, item in enumerate(value):
            if not isinstance(item, dict) or item.get("type") != "Feature":
                raise ValueError(f"items[{index}] must be a STAC Item JSON object (type == 'Feature').")
        return value

    @field_validator("base")
    @classmethod
    def validate_base(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if value.get("type") != "Collection":
            raise ValueError("base must be a STAC Collection JSON object (type == 'Collection').")
        if not value.get("id"):
            raise ValueError("base must have an id.")
        return value

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        proposed = value.strip()
        if not proposed or proposed.strip("-_") == "":
            raise ValueError("id must not be empty or only separators.")
        if "/" in proposed or "\\" in proposed:
            raise ValueError("id must not contain '/' or '\\': ids are flat.")
        if any(segment in {"", ".", ".."} for segment in proposed.split("-")):
            raise ValueError("id must not contain empty, '.', or '..' segments.")
        return proposed

    @field_validator("source_folder")
    @classmethod
    def validate_source_folder(cls, value: str | None) -> str | None:
        if value is None:
            return None
        folder = value.strip()
        if not folder:
            raise ValueError("source_folder must not be empty.")
        if is_http_url(folder):
            raise ValueError(
                "source_folder must be a local directory or an s3:// prefix, not an http(s) URL. "
                "Use item_links to suggest from remote item URLs."
            )
        return folder.rstrip("/")

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
        target = value.strip()
        if not target:
            raise ValueError("target must include a destination folder or prefix.")
        if is_http_url(target):
            raise ValueError("target must be an S3 prefix or a local folder path, not an http(s) URL.")
        if target.lower().endswith(".json"):
            raise ValueError(
                f"target {target!r} must be the destination folder; the object filename "
                "(collection.json) is appended automatically."
            )
        if target.lower().startswith("s3://"):
            raise ValueError(
                "target must be relative to the bucket; do not include s3://bucket — "
                "pass the bucket in the `bucket` field instead."
            )
        return target.rstrip("/")

    @model_validator(mode="after")
    def validate_sources_and_export(self) -> SuggestCollectionRequest:
        """At least one item source (or base); source_folder is exclusive and self-exporting."""

        if self.source_folder is not None:
            conflicts = [
                name
                for name, value in (
                    ("items", self.items),
                    ("item_links", self.item_links),
                    ("bucket", self.bucket),
                    ("target", self.target),
                    ("local_output", self.local_output),
                )
                if value
            ]
            if conflicts:
                raise ValueError(
                    f"source_folder is mutually exclusive with {', '.join(conflicts)}: the "
                    "folder provides the items and receives collection.json itself."
                )
        elif not self.items and not self.item_links and self.base is None:
            raise ValueError("Provide items, item_links, source_folder, or a base collection to suggest from.")

        credentials = (self.endpoint_url, self.access_key_id, self.secret_access_key)
        if any(credentials) and not all(credentials):
            missing = [name for name, value in zip(("endpoint_url", "access_key_id", "secret_access_key"), credentials) if not value]
            raise ValueError(
                f"Missing S3 connection fields: {', '.join(missing)}. Provide all of "
                "endpoint_url, access_key_id, and secret_access_key, or none of them "
                "to use the service-level S3_* environment configuration."
            )
        return self


class SuggestCollectionResponse(BaseModel):
    """Suggested Collection JSON plus derivation warnings for the builder form."""

    stac_object: dict[str, Any] = Field(description="The suggested STAC Collection as JSON.")
    warnings: list[str] = Field(default_factory=list)
    status: str = Field(default="suggested", description="`suggested` (preview) or `completed` (exported).")
    exported: bool = Field(default=False, description="True when `target`/`source_folder` was given and written.")
    collection_uri: str | None = Field(
        default=None,
        description="Full location of the written collection.json: a full https:// URL when "
        "`endpoint_url` is known (request value or S3_ENDPOINT_URL), an s3:// URI otherwise, "
        "or a local path for `local_output`.",
    )
    item_uris: list[str] = Field(
        default_factory=list,
        description="Full locations of the written (or re-linked) item files: full https:// URLs "
        "when `endpoint_url` is known, s3:// URIs otherwise, or local paths for `local_output`.",
    )

    model_config = ConfigDict(extra="forbid")
