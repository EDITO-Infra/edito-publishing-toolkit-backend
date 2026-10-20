"""Use-case logic for the STAC collection-suggestion feature.

Step-wise pipeline (each step logged):

1. get the items — inline ``items``, fetched ``item_links``, or the
   ``*.json`` files under ``source_folder``; every item is schema-validated;
2. derive the collection — extent (union of the items), license via SPDX
   matching, keywords, id proposal;
3. attach the items — pystac tree (target export) or in-place re-linking
   (source_folder export);
4. save — local folder, or staged locally and uploaded to the ``s3://`` prefix.

Without ``target``/``source_folder`` the result is pure metadata: the
suggested JSON feeds the STAC Collection builder form, which owns export
targets.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ept.infrastructure.formats.stac import geometry_bbox
from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.fetch import fetch_json
from ept.infrastructure.formats.stac.licenses import match_spdx_ids
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    full_s3_url,
    is_s3_uri,
    s3_endpoint_url,
    slugify,
)
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .export import export_into_source_folder, export_tree, read_folder_items
from .models import SuggestCollectionRequest

logger = logging.getLogger(__name__)

_WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]


def _s3_connection(request: SuggestCollectionRequest) -> S3ConnectionParameters | None:
    """Explicit connection parameters (already all-or-nothing via the model), else None."""

    if not request.endpoint_url:
        return None
    return S3ConnectionParameters(
        endpoint_url=request.endpoint_url,
        access_key_id=request.access_key_id,  # type: ignore[arg-type]  # model enforces all-or-nothing
        secret_access_key=request.secret_access_key,  # type: ignore[arg-type]
        session_token=request.session_token,
    )


def _resolve_target(target: str, *, local_output: str | None, bucket: str | None) -> str:
    """Full destination folder: s3://bucket/<target>, or a local folder path."""
    if local_output:
        return local_output
    if not bucket:
        raise ValueError(
            "bucket is required: the bearer token carries no username from which to "
            "derive the default oidc-<username> bucket."
        )
    return f"s3://{bucket}/{target.lstrip('/')}"


def suggest_collection(
    request: SuggestCollectionRequest,
    default_username: str | None = None,
    default_bucket: str | None = None,
) -> dict[str, Any]:
    """Suggest a STAC Collection from items (and/or a base collection); optionally export it."""

    warnings: list[str] = []
    s3 = _s3_connection(request)

    # Step 1 — get the items and validate each one.
    rel_names: list[str] | None = None
    if request.source_folder is not None:
        items, rel_names, folder_warnings = read_folder_items(request.source_folder, s3)
        warnings.extend(folder_warnings)
        logger.info("Step 1: %d item(s) read from source_folder %s", len(items), request.source_folder)
    else:
        items = list(request.items)
        items.extend(_fetch_linked_items(request.item_links, warnings))
        logger.info(
            "Step 1: %d item(s) gathered (inline=%d, links=%d)",
            len(items),
            len(request.items),
            len(request.item_links),
        )
    if request.item_links and not items and request.base is None:
        raise ValueError("None of the item_links could be fetched as STAC Items and no base collection was given.")

    # Step 2 — derive the collection metadata from the items.
    stac_object = _derive_collection(request, items, default_username, warnings)
    logger.info("Step 2: collection '%s' suggested (license=%s)", stac_object["id"], stac_object["license"])

    # Steps 3+4 — attach the items and export when asked; otherwise preview only.
    collection_uri: str | None = None
    item_uris: list[str] = []
    if request.source_folder is not None:
        stac_object, collection_uri, item_uris = export_into_source_folder(
            stac_object,
            items,
            rel_names,  # type: ignore[arg-type]  # set by read_folder_items above
            request.source_folder,
            overwrite=request.overwrite,
            s3=s3,
        )
    elif request.target is not None or request.local_output is not None:
        folder = _resolve_target(
            request.target or request.local_output,
            local_output=request.local_output,
            bucket=request.bucket or default_bucket,
        )
        stac_object, collection_uri, item_uris = export_tree(
            stac_object,
            items,
            folder,
            overwrite=request.overwrite,
            s3=s3,
            warnings=warnings,
        )
    else:
        warnings.append("links are empty: root and item links are wired by the STAC builder or assemble")

    status = "completed" if collection_uri else "suggested"
    endpoint = s3_endpoint_url(s3) or ""
    logger.info(
        "Suggested STAC Collection id=%s items=%d links=%d base=%s exported=%s target=%s warnings=%d",
        stac_object["id"],
        len(items),
        len(request.item_links),
        bool(request.base),
        collection_uri is not None,
        collection_uri,
        len(warnings),
    )
    return {
        "stac_object": stac_object,
        "warnings": warnings,
        "status": status,
        "exported": collection_uri is not None,
        "collection_uri": _full_uri(endpoint, collection_uri),
        "item_uris": [_full_uri(endpoint, uri) for uri in item_uris],
    }


def _full_uri(endpoint: str, uri: str | None) -> str | None:
    """Full https URL for an s3:// URI (like the other features return); local paths pass through."""

    if uri is None or not is_s3_uri(uri):
        return uri
    return full_s3_url(endpoint, uri.removeprefix("s3://"))


def _derive_collection(
    request: SuggestCollectionRequest,
    items: list[dict[str, Any]],
    default_username: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Derive the Collection JSON: extent, license (SPDX matching), keywords, id."""

    base = request.base
    slug = _slugify(request.title or (base.get("title") if base else None) or _first_item_id(items) or "collection")

    collection_id = _propose_id(request.id, base, slug, default_username, warnings)
    extent = _extent(items, base, warnings)

    stac_object: dict[str, Any] = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "id": collection_id,
        "description": (
            request.description
            or (base.get("description") if base else None)
            or request.title
            or collection_id
        ),
        "license": (
            request.license
            or (base.get("license") if base else None)
            or _license_from_items(items, warnings)
            or "other"
        ),
        "extent": extent,
        # Links are wired at export time, or by the STAC builder / assemble later.
        "links": [],
    }
    if request.title or (base and base.get("title")):
        stac_object["title"] = request.title or (base.get("title") if base else None)
    if base and base.get("providers"):
        stac_object["providers"] = base["providers"]
    if base and base.get("stac_extensions"):
        stac_object["stac_extensions"] = base["stac_extensions"]
    licenses = _distinct_item_licenses(items)
    if len(licenses) > 1:
        # Non-standard summary field; `license` stays 'other' — STAC's core
        # schema only allows one SPDX id there.
        stac_object["licenses"] = licenses
        warnings.append(
            f"items carry multiple licenses {licenses}: listed under 'licenses'; "
            "the 'license' field stays 'other' (STAC allows one SPDX id)"
        )
    keywords = _keywords_from_items(items, base)
    if keywords:
        stac_object["keywords"] = keywords

    if not request.description and not (base and base.get("description")):
        warnings.append("description fell back to title/id: set an explicit description in the builder form")

    validate_collection(stac_object)
    return stac_object


def _fetch_linked_items(links: list[str], warnings: list[str]) -> list[dict[str, Any]]:
    """Fetch Item JSONs for ``item_links``; unreadable links warn and are skipped."""
    items: list[dict[str, Any]] = []
    for link in links:
        try:
            document = fetch_json(link)
        except UnsafeUrlError as exc:
            warnings.append(f"item link '{link}' skipped: {exc}")
            continue
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"item link '{link}' skipped: {_exc_message(exc)}")
            continue
        if document.get("type") != "Feature":
            warnings.append(f"item link '{link}' skipped: not a STAC Item (type != 'Feature').")
            continue
        items.append(document)
    return items


def _exc_message(exc: Exception) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _distinct_item_licenses(items: list[dict[str, Any]]) -> list[str]:
    """Distinct license references across items, first-seen order."""
    found: list[str] = []
    for item in items:
        found.extend(_item_license_candidates(item))
    return list(dict.fromkeys(found))


def _license_from_items(items: list[dict[str, Any]], warnings: list[str]) -> str | None:
    """License shared by all items, else None.

    Scanned per item, in order of authority: ``license`` (top-level or
    ``properties.license``), ISO-derived ``properties.licenses[].id``, then
    SPDX ids recovered from ``properties.constraints[].text`` prose. Items
    disagreeing on the license cannot merge into one collection license; the
    distinct set is surfaced via ``licenses`` on the suggested collection.
    """

    distinct = _distinct_item_licenses(items)
    if not distinct:
        return None
    if len(distinct) > 1:
        warnings.append(
            f"items carry conflicting licenses {distinct}: falling back to the license default"
        )
        return None
    warnings.append(f"license '{distinct[0]}' derived from the items")
    return distinct[0]


def _keywords_from_items(items: list[dict[str, Any]], base: dict[str, Any] | None) -> list[str]:
    """Union of item keywords (first-seen order), merged with the base's."""
    keywords: list[str] = []
    if base and isinstance(base.get("keywords"), list):
        keywords.extend(str(k) for k in base["keywords"] if str(k).strip())
    for item in items:
        item_keywords = (item.get("properties") or {}).get("keywords")
        if isinstance(item_keywords, list):
            keywords.extend(str(k) for k in item_keywords if str(k).strip())
    return list(dict.fromkeys(keywords))


def _item_license_candidates(item: dict[str, Any]) -> list[str]:
    """License references found in one item, most authoritative first."""

    properties = item.get("properties") or {}
    candidates: list[str] = []
    for value in (item.get("license"), properties.get("license")):
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    for entry in properties.get("licenses") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip():
            candidates.append(entry["id"].strip())
    for constraint in properties.get("constraints") or []:
        text = constraint.get("text") if isinstance(constraint, dict) else None
        if isinstance(text, str):
            candidates.extend(match_spdx_ids(text))
    return list(dict.fromkeys(candidates))


def _first_item_id(items: list[dict[str, Any]]) -> str | None:
    return next((str(item.get("id")) for item in items if item.get("id")), None)


def _slugify(value: str) -> str:
    return slugify(value, "collection")


def _propose_id(
    explicit: str | None,
    base: dict[str, Any] | None,
    slug: str,
    default_username: str | None,
    warnings: list[str],
) -> str:
    if explicit:
        return explicit
    if base:
        # Ids are flat: keep only the base id's last segment and join with '-'.
        base_segment = str(base["id"]).rstrip("/").rsplit("/", 1)[-1]
        proposed = f"{base_segment}-{slug}"
        warnings.append(f"id derived from the base collection id and title slug: '{proposed}'")
        return proposed
    if not default_username:
        raise ValueError(
            "Cannot propose a collection id: no explicit id was given and no username could "
            "be derived from your bearer token. Pass an explicit id."
        )
    proposed = f"{default_username}-{slug}"
    warnings.append(f"id proposed from your username and title slug: '{proposed}' — it must be unique within your catalog")
    return proposed


def _extent(items: list[dict[str, Any]], base: dict[str, Any] | None, warnings: list[str]) -> dict[str, Any]:
    """Spatial/temporal union of the items; falls back to the base extent, else a world placeholder."""

    bboxes = [bbox for bbox in (_item_bbox(item) for item in items) if bbox]
    intervals = [interval for interval in (_item_interval(item, warnings) for item in items) if interval]

    if bboxes or intervals:
        return {
            "spatial": {"bbox": [_union_bbox(bboxes) or list(_WORLD_BBOX)]},
            "temporal": {"interval": [_union_interval(intervals)]},
        }
    if base and "extent" in base:
        warnings.append("no item geometry or datetime available: extent copied from the base collection")
        return base["extent"]
    warnings.append(
        "no item geometry or datetime available: extent is a world-wide open-interval placeholder"
    )
    return {"spatial": {"bbox": [list(_WORLD_BBOX)]}, "temporal": {"interval": [[None, None]]}}


def _item_bbox(item: dict[str, Any]) -> list[float] | None:
    bbox = item.get("bbox")
    if isinstance(bbox, list) and len(bbox) in (4, 6) and all(isinstance(v, (int, float)) for v in bbox):
        return [float(v) for v in bbox]
    return geometry_bbox(item.get("geometry"))


def _item_interval(item: dict[str, Any], warnings: list[str]) -> tuple[datetime | None, datetime | None] | None:
    properties = item.get("properties") or {}
    item_id = item.get("id", "<unknown>")
    single = _parse_datetime(properties.get("datetime"), item_id, warnings)
    if single:
        return single, single
    start = _parse_datetime(properties.get("start_datetime"), item_id, warnings)
    end = _parse_datetime(properties.get("end_datetime"), item_id, warnings)
    if start or end:
        return start, end
    return None


def _parse_datetime(value: Any, item_id: str, warnings: list[str]) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        warnings.append(f"item '{item_id}': unparseable datetime '{value}' ignored")
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _union_bbox(bboxes: list[list[float]]) -> list[float] | None:
    """Elementwise min/max of the bboxes; mixed 2D/3D inputs are projected to 2D.

    honey: vertical extents of 2D items are lost when mixed with 3D ones; fine
    for collection-level summaries — revisit only if EDITO needs 3D extents.
    """
    if not bboxes:
        return None
    if any(len(bbox) != len(bboxes[0]) for bbox in bboxes):
        bboxes = [
            [bbox[0], bbox[1], bbox[3], bbox[4]] if len(bbox) == 6 else [float(v) for v in bbox]
            for bbox in bboxes
        ]
    dimensions = len(bboxes[0])
    return [
        min(bbox[index] for bbox in bboxes) if index < dimensions // 2 else max(bbox[index] for bbox in bboxes)
        for index in range(dimensions)
    ]


def _union_interval(intervals: list[tuple[datetime | None, datetime | None]]) -> list[str | None]:
    starts = [start for start, _ in intervals if start]
    ends = [end for _, end in intervals if end]
    # STAC allows open-ended intervals as null; when both sides are open the
    # builder's placeholder warning still applies.
    if not starts and not ends:
        return [None, None]

    def iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    return [iso(min(starts)) if starts else None, iso(max(ends)) if ends else None]
