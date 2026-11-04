"""Declarative conversion registry for the ARCO STAC Item feature.

Loaded from ``conversions.toml`` next to this module, in the same spirit as
the job runner's ``operations.toml``: declarations are imported eagerly so a
broken entry fails at startup, and conversion services are never imported by
the feature code itself.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ept.core.contracts import import_object

__all__ = ["CONVERSIONS", "Conversion", "detect_conversion", "resolve_conversion"]

# Asset values a conversion may map into its request payload.
_ASSET_VALUES = ("href", "bucket", "target")
# Template placeholders for ``values``.
_TEMPLATE_CONTEXT = ("source", "suffix", "s3_prefix")
# Request-scoped S3 credential fields injected when the request model declares them.
_CREDENTIAL_FIELDS = ("endpoint_url", "access_key_id", "secret_access_key", "session_token")


@dataclass(frozen=True)
class Conversion:
    """One declared conversion: a feature service plus how to drive it."""

    key: str
    service: Any
    request_model: type[BaseModel]
    asset_key: str
    media_type: str
    fields: dict[str, str]
    values: dict[str, str]
    detect_media: tuple[str, ...] = ()
    detect_suffixes: tuple[str, ...] = ()
    detect_markers: tuple[str, ...] = ()
    result_field: str | None = None

    def matches(self, *, href: str, media_type: str) -> bool:
        """Auto-detection: does this asset look like this conversion's input?"""
        if any(marker in media_type for marker in self.detect_media):
            return True
        path = href.split("?", 1)[0].lower()
        if self.detect_suffixes and path.endswith(self.detect_suffixes):
            return True
        return any(marker in href for marker in self.detect_markers)

    def build_request(
        self,
        *,
        href: str,
        bucket: str,
        target: str,
        source_key: str,
        s3_prefix: str,
        suffix: str,
        credentials: dict[str, str | None],
        overwrite: bool,
    ) -> BaseModel:
        """Build and validate the sub-feature request for one asset.

        Request-scoped S3 credentials are merged into the payload before
        validation: some request models declare them required (upload_s3),
        others optional with environment fallback. The ARCO request's
        ``overwrite`` flag is likewise injected into any request model that
        declares an ``overwrite`` field — without it, a re-run with
        ``overwrite=true`` would fail on outputs left by a previous run
        because the sub-features default to overwrite=False.
        """
        asset_values = {"href": href, "bucket": bucket, "target": target}
        payload: dict[str, Any] = {
            field_name: asset_values[name] for name, field_name in self.fields.items()
        }
        context = {"source": source_key, "suffix": suffix, "s3_prefix": s3_prefix, "target": target}
        for field_name, template in self.values.items():
            payload[field_name] = template.format(**context)
        payload.update(
            {
                name: value
                for name, value in credentials.items()
                if value is not None and name in self.request_model.model_fields
            }
        )
        if "overwrite" in self.request_model.model_fields:
            payload["overwrite"] = overwrite
        return self.request_model.model_validate(payload)


def _load() -> dict[str, Conversion]:
    """Build the conversion registry from ``conversions.toml``."""
    manifest = Path(__file__).with_name("conversions.toml")
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    conversions: dict[str, Conversion] = {}
    for key, table in data.get("conversions", {}).items():
        service = import_object(str(table["service"]))
        if not callable(service):
            raise TypeError(f"Conversion '{key}': service '{table['service']}' is not callable.")
        model = import_object(str(table["request_model"]))
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError(f"Conversion '{key}': request_model is not a BaseModel.")
        detect = table.get("detect", {})
        conversions[key] = Conversion(
            key=key,
            service=service,
            request_model=model,
            asset_key=str(table["asset_key"]),
            media_type=str(table["media_type"]),
            fields={str(k): str(v) for k, v in table.get("fields", {}).items()},
            values={str(k): str(v) for k, v in table.get("values", {}).items()},
            detect_media=tuple(str(m).lower() for m in detect.get("media_type", [])),
            detect_suffixes=tuple(str(s).lower() for s in detect.get("suffixes", [])),
            detect_markers=tuple(str(m).lower() for m in detect.get("href_markers", [])),
            result_field=str(table["result_field"]) if table.get("result_field") else None,
        )
    if not conversions:
        raise ValueError("conversions.toml declares no conversions.")
    return conversions


CONVERSIONS: dict[str, Conversion] = _load()


def resolve_conversion(key: str) -> Conversion | None:
    """Return the declared conversion with ``key``, or None."""
    return CONVERSIONS.get(key)


def detect_conversion(*, href: str, media_type: str) -> Conversion | None:
    """First declared conversion whose auto-detection matches the asset."""
    href = str(href)
    media_type = str(media_type).lower()
    for conversion in CONVERSIONS.values():
        if conversion.matches(href=href, media_type=media_type):
            return conversion
    return None
