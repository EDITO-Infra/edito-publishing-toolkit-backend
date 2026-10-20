"""Public models for the ARCO STAC Item feature."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import is_s3_uri, safe_export_name, with_file_name

from .conversions import CONVERSIONS

__all__ = [
    "ArcoConversionResult",
    "ArcoStacItemRequest",
    "ArcoStacItemResponse",
]


class _OpenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArcoStacItemRequest(S3ConnectionParameters):
    """ARCO-ify the data assets of an existing STAC Item.

    The Item JSON is read from `source_uri` (local path, `s3://` URI, or
    HTTP(S) URL) — typically the output of `POST /v1/metadata/iso-to-stac-item`.
    Its assets are inspected and converted:

    - **Declared conversions** (`conversions.toml` in the feature) map media
      types, href suffixes, and URL markers to conversion features. NetCDF
      assets auto-convert with `nc-to-zarr`, `.gdb`/`.gdb.zip` with
      `gdb-to-parquet`; a plain `.zip` is ambiguous and is never
      auto-converted.
    - **Explicit mappings win.** `conversions` maps an asset key, an href
      file name, or an href path fragment (a pattern containing `/`) to a
      conversion key declared in `conversions.toml`
      (`nc-to-zarr`, `gdb-to-parquet`, `upload`) for anything auto-detection
      cannot decide, e.g. `{"downloads/data.zip": "gdb-to-parquet"}`.
      `upload` hosts the asset in S3 unchanged.

    Output locations are chosen separately:

    - `item_target` — prefix for the updated Item JSON
      (`s3://<bucket>/<item_target>/<item_id>.json`; a value ending in
      `.json` names the file itself). Omit it to replace the Item at its
      source location in place (S3 sources only), or to write a link/local
      source's updated Item to the default location
      `s3://<bucket>/<item_id>.json` (bucket root).
    - `assets_target` — prefix for converted outputs. Defaults to
      `item_target` when set, else to the source Item's folder (S3
      sources) or the bucket root.
    """

    # Redeclared optional: S3 credentials are only needed for S3 input/output;
    # when omitted the sub-features fall back to the S3_* environment.
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
            "STAC Item JSON: local path on the EPT service filesystem, "
            "s3:// URI, or HTTP(S) URL."
        ),
    )
    bucket: str | None = Field(
        default=None,
        description=(
            "Destination S3 bucket for the updated Item and converted assets. "
            "Defaults to your personal EDITO bucket `oidc-<username>` derived "
            "from your bearer token."
        ),
    )
    item_target: str | None = Field(
        default=None,
        description=(
            "Prefix within the bucket for the updated Item JSON — the file is "
            "written to `s3://<bucket>/<item_target>/<item_id>.json`, or the "
            "value itself when it ends in `.json`. Omit to "
            "replace the Item at its source location in place (S3 sources only)."
        ),
    )
    assets_target: str | None = Field(
        default=None,
        description=(
            "Prefix within the bucket for converted asset outputs. Defaults "
            "to item_target when set, else to the source Item's folder."
        ),
    )
    item_id: str | None = Field(
        default=None,
        description="Override the STAC Item id; defaults to the Item's existing id.",
    )
    overwrite: bool = Field(default=False, description="Replace existing outputs (Item JSON and conversion targets).")
    conversions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Explicit asset-to-conversion mappings for ambiguous assets. Keys "
            "are asset keys, href file names, or href path fragments "
            "(containing '/'); bare names never match directory segments of a "
            "path. Values are conversion keys declared in conversions.toml "
            "(e.g. nc-to-zarr, gdb-to-parquet, upload)."
        ),
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

    @field_validator("item_target", "assets_target")
    @classmethod
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        target = value.strip().strip("/")
        if not target or target.lower().startswith("s3://"):
            raise ValueError("targets must be prefixes relative to bucket; do not include s3://bucket.")
        return target

    @field_validator("conversions")
    @classmethod
    def validate_conversions(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = sorted(set(value.values()) - set(CONVERSIONS))
        if unknown:
            raise ValueError(
                f"Unknown conversion(s): {', '.join(unknown)}. "
                f"Declared conversions: {', '.join(CONVERSIONS)}."
            )
        return value

    def resolved_item_uri(self, source_uri: str, item_id: str, *, bucket: str) -> str:
        """The s3:// URI the updated Item JSON is written to.

        ``bucket`` is the resolved destination bucket (explicit or derived
        from the bearer token). A link or local source with no
        ``item_target`` falls back to the default output
        ``s3://<bucket>/<item_id>.json`` at the bucket root.
        """
        name = safe_export_name(item_id, fallback="item")
        if self.item_target is None:
            if is_s3_uri(source_uri):
                return source_uri
            return f"s3://{bucket}/{name}.json"
        return f"s3://{bucket}/{with_file_name(self.item_target, f'{name}.json')}"


class ArcoConversionResult(_OpenApiModel):
    """Outcome of one asset conversion."""

    source_asset_key: str = Field(description="Item asset key the conversion was based on.")
    feature: str = Field(description="Conversion key from conversions.toml (e.g. nc-to-zarr, gdb-to-parquet).")
    asset_key: str = Field(description='Asset key added to the Item (e.g. "zarr", "parquet").')
    target_uri: str = Field(description="S3 URI of the converted output (or uploaded copy).")
    layers: list[str] | None = Field(
        default=None,
        description="Layer name(s) this converted output file contains (gdb/shp-to-parquet only).",
    )


class ArcoStacItemResponse(_OpenApiModel):
    """Result of the ARCO STAC Item pipeline."""

    item_id: str = Field(description="Identifier of the STAC Item.")
    stac_item: dict[str, Any] = Field(description="The updated STAC Item JSON, including the ARCO assets.")
    source_uri: str = Field(description="The Item JSON the pipeline started from.")
    target_uri: str = Field(description="Full s3:// URI of the updated Item JSON.")
    conversions: list[ArcoConversionResult] = Field(
        default_factory=list,
        description="One entry per converted output file (or uploaded copy).",
    )
    stage_timings_ms: dict[str, int] = Field(
        default_factory=dict,
        description='Wall time per pipeline stage in ms, keyed like "convert:zip:gdb-to-parquet".',
    )
    status: str = Field(description='"completed" when the updated Item JSON was written.')
