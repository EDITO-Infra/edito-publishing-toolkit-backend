"""Adapters: translate between infrastructure DTOs and the STAC item use case.

Owns all translation — NetCDF summary → STAC item content, dataset tokens →
collection match, item dict → local path or S3 key — so ``service.py`` stays
orchestration only.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ept.infrastructure.formats.netcdf import DatasetSummary
from ept.infrastructure.formats.stac import (
    StacAssetInput,
    StacItemInput,
    StacLinkInput,
    bbox_to_geometry,
)
from ept.infrastructure.formats.stac import item as stac_item
from ept.infrastructure.services.editostacapi import collection_href
from ept.infrastructure.storage.s3 import is_s3_uri, s3_url_to_fs, slugify

from .models import NetcdfToStacItemRequest

logger = logging.getLogger(__name__)

# CF-recommended global attributes promoted into item properties.
GLOBAL_ATTR_KEYS = (
    "Conventions",
    "title",
    "summary",
    "institution",
    "source",
    "history",
    "references",
    "comment",
    "license",
    "product_version",
    "processing_level",
)
VARIABLE_KEYS = ("standard_name", "long_name", "units")

# STAC 1.1 requires `license` to be an SPDX identifier (or "proprietary"/
# "various"); CF license attributes are free text and get dropped.
SPDX_LICENSE_PATTERN = re.compile(r"^[\w\-\.\+]+$")


def build_item(
    request: NetcdfToStacItemRequest,
    summary: DatasetSummary,
    collection_id: str | None,
    item_id: str,
) -> dict[str, Any]:
    """Translate the NetCDF summary into a validated STAC Item dict.

    ``item_id`` is resolved by the service (explicit request id, or the
    username-namespaced dataset identifier). ``collection_id`` is optional —
    standalone items are valid STAC and can be attached to a collection
    later (e.g. an add_stac_item feature).
    """
    bbox, instant, start_datetime, end_datetime = resolve_extents(request, summary)
    geometry, normalized_bbox = bbox_to_geometry(bbox)
    properties = item_properties(summary, request.extra_properties)
    if instant is not None:
        properties["datetime"] = instant.isoformat()
    else:
        properties["start_datetime"] = start_datetime.isoformat()
        properties["end_datetime"] = end_datetime.isoformat()

    payload = stac_item.build_item(
        StacItemInput(
            id=item_id,
            geometry=geometry,
            bbox=normalized_bbox,
            properties=properties,
            assets={
                "data": StacAssetInput(
                    href=request.source_uri,
                    title=summary.attrs.get("title"),
                    type=media_type(request.source_uri),
                    roles=["data"],
                )
            },
            collection=collection_id,
            links=_collection_link(collection_id),
        )
    )
    stac_item.validate_item(payload)
    return payload


def _collection_link(collection_id: str | None) -> list[StacLinkInput]:
    """STAC 1.1 requires a matching ``collection`` link when ``collection`` is set."""
    if not collection_id:
        return []
    return [
        StacLinkInput(
            href=collection_href(collection_id),
            rel="collection",
            type="application/json",
        )
    ]


def resolve_extents(
    request: NetcdfToStacItemRequest, summary: DatasetSummary
) -> tuple[list[float], datetime | None, datetime | None, datetime | None]:
    """Spatial/temporal extents: request overrides first, then the dataset's own.

    Returns ``(bbox, instant, start, end)`` where exactly one of ``instant``
    (dataset date attributes such as ``date_created``) or the ``(start, end)``
    range (time coordinate, ``time_coverage_*`` attributes, or request
    override) is set. Raises ``ValueError`` naming the request fields to set
    when neither the request nor the dataset yields an extent — STAC Items
    require one.
    """
    bbox = request.bbox if request.bbox is not None else summary.bbox
    if bbox is None:
        raise ValueError(
            "No spatial extent: the dataset has no CF lat/lon coordinates or "
            "geospatial_lat/lon_min/max attributes. Provide `bbox` in the request."
        )
    if request.start_datetime is not None and request.end_datetime is not None:
        return bbox, None, ensure_utc(request.start_datetime), ensure_utc(request.end_datetime)
    if summary.temporal is not None:
        return (
            bbox,
            None,
            ensure_utc(summary.temporal[0]),
            ensure_utc(summary.temporal[1]),
        )
    if summary.instant is not None:
        return bbox, ensure_utc(summary.instant), None, None
    raise ValueError(
        "No temporal extent: the dataset has no time coordinate, "
        "time_coverage_start/end attributes, or date attributes such as "
        "date_created. Provide `start_datetime` and `end_datetime` in the request."
    )


def suggested_collection_id(summary: DatasetSummary) -> str | None:
    """Kebab-case collection-id suggestion from official vocabulary terms.

    Prefers the first data-variable CF ``standard_name`` (the CF standard
    name table, https://cfconventions.org), falling back to the first
    variable name. Users can adopt it as-is or namespace it, e.g.
    ``projects/<projectid>/<suggestion>``.
    """
    name = next(
        (var.standard_name for var in summary.variables.values() if var.standard_name),
        None,
    ) or next((name for name in summary.variables), None)
    if not name:
        return None
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or None


def item_properties(summary: DatasetSummary, extra: dict[str, Any]) -> dict[str, Any]:
    """CF global attributes plus a per-variable summary, then caller extras."""
    properties: dict[str, Any] = {
        key: summary.attrs[key] for key in GLOBAL_ATTR_KEYS if key in summary.attrs
    }
    if "license" in properties and not SPDX_LICENSE_PATTERN.fullmatch(properties["license"]):
        logger.info(
            "Dropping non-SPDX CF license from item properties (kept in dataset attrs): %s",
            properties.pop("license"),
        )
    properties["variables"] = {
        name: {key: value for key in VARIABLE_KEYS if (value := getattr(var, key)) is not None}
        for name, var in summary.variables.items()
    }
    properties.update(extra)
    return properties


def default_item_id(source_uri: str) -> str:
    """Source file name without extension, from any URI shape."""
    name = PurePosixPath(urlsplit(source_uri).path).stem or Path(source_uri).stem
    if not name:
        raise ValueError(f"Cannot derive an item id from source: {source_uri}. Pass item_id.")
    return name


# Dataset attributes carrying an identifier, in preference order — the same
# fields the ISO transformer and the suggesters fall back to.
ITEM_ID_ATTR_KEYS = ("id", "identifier", "title")


def dataset_item_id(summary: DatasetSummary, source_uri: str) -> str:
    """The dataset's own identifier (``id``/``identifier``/``title`` attr), else the source file name."""

    for key in ITEM_ID_ATTR_KEYS:
        value = summary.attrs.get(key)
        if value and value.strip():
            return value.strip()
    return default_item_id(source_uri)


def proposed_item_id(summary: DatasetSummary, source_uri: str, default_username: str | None) -> str:
    """Default item id when the caller gives none: dataset identifier or source
    name, namespaced with the username so ids stay unique per owner (same
    scheme as the catalog/collection suggesters)."""

    base = slugify(dataset_item_id(summary, source_uri), "item")
    return f"{default_username}-{base}" if default_username else base


def media_type(source_uri: str) -> str | None:
    return "application/x-netcdf" if source_uri.lower().split("?")[0].endswith(".nc") else None


def ensure_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# ─── Collection matching ─────────────────────────────────────────────────────


def find_matching_collection(
    summary: DatasetSummary, collections: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """First collection whose text mentions any variable name or CF standard_name."""
    tokens = collection_tokens(summary)
    for collection in collections:
        haystack = json.dumps(collection, default=str).lower()
        if any(token in haystack for token in tokens):
            return collection
    return None


def collection_tokens(summary: DatasetSummary) -> list[str]:
    """Lowercase match tokens: CF standard_names then variable names (≥3 chars)."""
    names = [name.lower() for name in summary.variables if len(name) >= 3]
    return [*dict.fromkeys([*summary.standard_names, *names])]


# ─── Persistence (local path or S3 key) ─────────────────────────────────────


def write_item(
    item: dict[str, Any],
    target_uri: str,
    *,
    storage_options: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> str:
    payload = json.dumps(item, indent=2, ensure_ascii=False)
    if is_s3_uri(target_uri):
        fs, path = s3_url_to_fs(target_uri, storage_options=storage_options or {})
        if not overwrite and fs.exists(path):
            raise FileExistsError(
                f"Target already exists: {target_uri}. Set overwrite=true to replace it."
            )
        fs.pipe_file(path, payload.encode("utf-8"))
        return target_uri
    target = Path(target_uri)
    if not overwrite and target.exists():
        raise FileExistsError(
            f"Target already exists: {target}. Set overwrite=true to replace it."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return str(target)
