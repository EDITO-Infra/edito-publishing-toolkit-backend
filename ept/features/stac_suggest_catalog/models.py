"""Public models for the STAC catalog-suggestion feature."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ept.infrastructure.storage.s3 import is_http_url
from ept.infrastructure.utils.field_types import LocalOutput

__all__ = ["SuggestCatalogRequest", "SuggestCatalogResponse"]


class SuggestCatalogRequest(BaseModel):
    """Inputs for suggesting a STAC Catalog from collections and/or a base catalog.

    Two modes:

    - **Preview** (no ``source_folder``): inline ``collections`` and/or a
      ``base`` — pure metadata, the suggested JSON feeds the STAC Catalog
      builder form.
    - **Assemble** — a set of collection trees, from either/both of:
      ``source_folder`` (a local directory or ``s3://`` prefix whose
      subfolders each hold one self-contained Collection tree — the layout
      ``suggest/collection`` exports) or ``collection_links`` (URLs/URIs of
      ``collection.json`` files; each collection's item files are fetched via
      its ``item`` links). The catalog JSON is derived from those collections
      and the whole tree is exported into ``target`` (or ``local_output``):
      ``catalog.json`` at the root with relative child links, each collection
      copied under ``<id>/`` (its id, slugified when unsafe as a folder name)
      with its root/parent links re-pointed at the catalog and its item copies
      re-linked — a complete, publishable, self-contained tree.

    Catalog metadata is enriched from the collections in every mode: keywords
    are unioned and providers merged (deduplicated) from the collections.

    Requires ``Authorization: Bearer <access_token>`` (bearer carries the
    default personal bucket and the id proposal).
    """

    collections: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "STAC Collections that will become the catalog's children (preview "
            "mode). Only their presence/ids matter here; child links are wired "
            "at export time. Optional when `base` is given. Mutually exclusive "
            "with `source_folder`."
        ),
    )
    collection_links: list[str] = Field(
        default_factory=list,
        description=(
            "STAC Collection JSON locations to fetch — http(s) URLs (SSRF-"
            "guarded), s3:// URIs, or paths on the EPT filesystem. Each must "
            "be a valid Collection; its item files are fetched via its `item` "
            "links and the whole tree is copied into the export target. "
            "Unreadable links are reported as warnings and skipped. Combine "
            "with `source_folder`; without an export target this is a "
            "validated preview only."
        ),
    )
    source_folder: str | None = Field(
        default=None,
        description=(
            "Assemble mode: local directory or s3://bucket/prefix whose "
            "subfolders each contain one self-contained Collection tree "
            "(collection.json plus item files), as exported by "
            "suggest/collection. Each collection is copied under "
            "`<target>/<id>/` (id slugified when unsafe as a folder name) "
            "with links re-pointed at the new catalog. Mutually exclusive "
            "with `collections`; always exports, so `target` (or "
            "`local_output`) is required."
        ),
    )
    base: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Existing STAC Catalog (or Collection) to copy metadata from. Its "
            "id's last segment prefixes the proposed id — combine with your "
            "existing EDITO catalog from GET /v1/edito/stac/mycatalogs to get "
            "a unique flat id."
        ),
    )
    id: str | None = Field(
        default=None,
        description=(
            "Explicit catalog id (no '/' — ids are flat; EDITO's infrastructure "
            "root is added at publish time). When omitted, one is proposed: "
            "`<base id>-<slug>` with a base, else `<username>-<slug>` derived "
            "from your bearer token."
        ),
    )
    title: str | None = Field(default=None, description="Human-readable title; also seeds the id slug.")
    description: str | None = Field(default=None, description="Catalog description.")

    # ── export (assemble mode only: source_folder always exports) ───────────
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket name. Defaults to your personal EDITO bucket "
            "`oidc-<username>` derived from your bearer token. Other buckets your "
            "account can access — e.g. project buckets like `project-<projectid>` — "
            "are equally valid. Must be a bucket name without s3:// or an object "
            "key."
        ),
    )
    target: str | None = Field(
        default=None,
        description=(
            "Destination folder: an S3 prefix within `bucket`. `catalog.json` "
            "is written at the root, each collection under `<id>/`. Required "
            "with `source_folder` (unless `local_output` is set)."
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
            "Replace an existing catalog.json and collection copies. With "
            "overwrite=false, existing catalog.json or collection.json files "
            "raise FileExistsError; item files are always (re-)written."
        ),
    )

    # ── S3 connection (needed for s3:// source/targets) ──────────────────────
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
                {"required": ["collections"]},
                {"required": ["collection_links"]},
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

    @field_validator("collections")
    @classmethod
    def validate_collections(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for index, collection in enumerate(value):
            if not isinstance(collection, dict) or collection.get("type") != "Collection":
                raise ValueError(
                    f"collections[{index}] must be a STAC Collection JSON object (type == 'Collection')."
                )
        return value

    @field_validator("collection_links")
    @classmethod
    def validate_collection_links(cls, value: list[str]) -> list[str]:
        for index, link in enumerate(value):
            if not isinstance(link, str) or not link.strip():
                raise ValueError(f"collection_links[{index}] must be a non-empty URL or URI.")
        return value

    @field_validator("base")
    @classmethod
    def validate_base(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if value.get("type") not in ("Catalog", "Collection"):
            raise ValueError("base must be a STAC Catalog or Collection JSON object.")
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
                "Use `collections` to preview from collection JSON passed inline."
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
                f"target {target!r} must be the destination folder; catalog.json is "
                "appended automatically at its root."
            )
        if target.lower().startswith("s3://"):
            raise ValueError(
                "target must be relative to the bucket; do not include s3://bucket — "
                "pass the bucket in the `bucket` field instead."
            )
        return target.rstrip("/")

    @model_validator(mode="after")
    def validate_sources_and_export(self) -> SuggestCatalogRequest:
        """source_folder is assemble mode (exclusive with `collections`, always exports)."""

        if self.source_folder is not None:
            if self.collections:
                raise ValueError(
                    "source_folder is mutually exclusive with `collections`: the folder "
                    "provides the collections to assemble."
                )
            if not self.target and not self.local_output:
                raise ValueError(
                    "source_folder always exports: provide `target` (with `bucket`, which "
                    "defaults to your personal bucket) or `local_output`."
                )
        elif not self.collections and not self.collection_links and self.base is None:
            raise ValueError(
                "Provide collections, collection_links, source_folder, or a base catalog "
                "to suggest from."
            )

        credentials = (self.endpoint_url, self.access_key_id, self.secret_access_key)
        if any(credentials) and not all(credentials):
            missing = [name for name, value in zip(("endpoint_url", "access_key_id", "secret_access_key"), credentials) if not value]
            raise ValueError(
                f"Missing S3 connection fields: {', '.join(missing)}. Provide all of "
                "endpoint_url, access_key_id, and secret_access_key, or none of them "
                "to use the service-level S3_* environment configuration."
            )
        return self


class SuggestCatalogResponse(BaseModel):
    """Suggested Catalog JSON plus derivation warnings for the builder form."""

    stac_object: dict[str, Any] = Field(description="The suggested STAC Catalog as JSON.")
    warnings: list[str] = Field(default_factory=list)
    status: str = Field(default="suggested", description="`suggested` (preview) or `completed` (assembled + exported).")
    exported: bool = Field(
        default=False, description="True when `source_folder` assemble mode ran and the tree was written."
    )
    catalog_uri: str | None = Field(
        default=None,
        description="Full location of the written catalog.json: a full https:// URL when "
        "`endpoint_url` is known (request value or S3_ENDPOINT_URL), an s3:// URI otherwise, "
        "or a local path for `local_output`.",
    )
    collection_uris: list[str] = Field(
        default_factory=list,
        description="Full locations of the copied collection.json files, one per source collection: "
        "full https:// URLs when `endpoint_url` is known, s3:// URIs otherwise, or local paths "
        "for `local_output`.",
    )
    item_uris: list[str] = Field(
        default_factory=list,
        description="Full locations of the copied (and re-linked) item files: full https:// URLs "
        "when `endpoint_url` is known, s3:// URIs otherwise, or local paths for `local_output`.",
    )

    model_config = ConfigDict(extra="forbid")
