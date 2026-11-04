"""Use-case logic for the STAC Item builder.

Three input modes: build from fields, overlay fields onto a complete object
(``json``), or overlay onto an object fetched from ``source_url``. Links are
always rebuilt from the request so exported objects are predictable. Sync
pystac work and file/S3 I/O run via ``run_in_threadpool`` (routes await the
async fetch, which uses the SSRF-guarded ``read_stac`` primitive).
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pystac
from starlette.concurrency import run_in_threadpool

from ept.infrastructure.formats.stac import resolve_base
from ept.infrastructure.formats.stac.item import bbox_to_geometry, validate_item
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    is_s3_uri,
    resolve_s3_storage_options,
    s3_url_to_fs,
    with_file_name,
)

from .models import BuildItemRequest

logger = logging.getLogger(__name__)

_INTERNAL_FIELDS = {
    "stac_json",
    "source_url",
    "bucket",
    "target",
    "local_output",
    "overwrite",
    "endpoint_url",
    "access_key_id",
    "secret_access_key",
    "session_token",
}

_OBJECT_FILENAME = "item.json"


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


def _target_uri(folder: str) -> str:
    """Standard object path inside the destination folder."""
    return with_file_name(folder.rstrip("/"), _OBJECT_FILENAME)


def _s3_connection(request: BuildItemRequest) -> S3ConnectionParameters | None:
    """Explicit connection parameters, or None to use the service-level S3_* environment."""

    fields = (request.endpoint_url, request.access_key_id, request.secret_access_key)
    if not any(fields):
        return None
    if not all(fields):
        missing = [
            name
            for name, value in (
                ("endpoint_url", fields[0]),
                ("access_key_id", fields[1]),
                ("secret_access_key", fields[2]),
            )
            if not value
        ]
        raise ValueError(
            f"Missing S3 connection fields: {', '.join(missing)}. Provide all of "
            "endpoint_url, access_key_id, and secret_access_key, or none of them "
            "to use the service-level S3_* environment configuration."
        )
    return S3ConnectionParameters(
        endpoint_url=fields[0],
        access_key_id=fields[1],
        secret_access_key=fields[2],
        session_token=request.session_token,
    )


async def build_item(
    request: BuildItemRequest,
    *,
    http_client: httpx.AsyncClient,
    access_token: str | None = None,
    default_bucket: str | None = None,
) -> dict[str, Any]:
    """Load the base object (``json``/``source_url``), build the Item, export when ``target`` is set."""

    base = await resolve_base(request.stac_json, request.source_url, client=http_client, access_token=access_token)

    stac_object = await run_in_threadpool(_build_object, base, request)
    warnings = _structural_warnings(request, stac_object)

    target_uri = None
    if request.target or request.local_output:
        folder = _resolve_target(
            request.target or request.local_output,
            local_output=request.local_output,
            bucket=request.bucket or default_bucket,
        )
        target_uri = _target_uri(folder)
        stac_object.setdefault("links", []).append({"rel": "self", "href": target_uri})
        await run_in_threadpool(_export, request, stac_object, target_uri)

    exported = target_uri is not None
    logger.info(
        "Built STAC Item id=%s mode=%s exported=%s target=%s warnings=%d",
        stac_object.get("id", request.id),
        "overlay" if base is not None else "fields",
        exported,
        target_uri,
        len(warnings),
    )
    return {
        "stac_object": stac_object,
        "warnings": warnings,
        "exported": exported,
        "target_uri": target_uri,
        "status": "completed" if exported else "preview",
    }


def _build_object(base: dict[str, Any] | None, request: BuildItemRequest) -> dict[str, Any]:
    """Build the STAC object: from fields, or overlay fields onto the base."""

    if base is None:
        stac_object = _build_from_fields(request)
    else:
        stac_object = _overlay_fields(base, request)

    if request.links:
        stac_object["links"] = [dict(link) for link in request.links]
    stac_object.setdefault("links", [])
    # Drop pystac-style null link fields (e.g. "title": null): the schemas want strings.
    stac_object["links"] = [
        {key: value for key, value in link.items() if value is not None} for link in stac_object["links"]
    ]
    # STAC 1.1 item schema: a top-level `collection` field requires a matching
    # rel=collection link; synthesize the sibling href when absent.
    if stac_object.get("collection") and not any(
        link.get("rel") == "collection" for link in stac_object["links"]
    ):
        stac_object["links"].append({"rel": "collection", "href": "./collection.json"})
    # Schema-required keys: geometry may be null, assets may be empty.
    stac_object.setdefault("geometry", None)
    stac_object.setdefault("assets", {})
    stac_object.update(request.extra_fields)
    validate_item(stac_object)
    return stac_object


def _build_from_fields(request: BuildItemRequest) -> dict[str, Any]:
    properties = dict(request.properties)
    for field in ("title", "description"):
        value = getattr(request, field)
        if value is not None:
            properties[field] = value

    # An instant goes through pystac's datetime constructor parameter (it
    # serializes to the canonical properties.datetime); an interval goes into
    # properties directly. pystac ignores properties["datetime"] strings.
    instant = request.datetime or properties.get("datetime")
    start = request.start_datetime or properties.get("start_datetime")
    end = request.end_datetime or properties.get("end_datetime")
    constructor_datetime: datetime.datetime | None = None
    if instant:
        _validate_iso8601(instant, "datetime")
        constructor_datetime = datetime.datetime.fromisoformat(instant)
        for field in ("datetime", "start_datetime", "end_datetime"):
            properties.pop(field, None)
    elif start and end:
        _validate_iso8601(start, "start_datetime")
        _validate_iso8601(end, "end_datetime")
        properties["start_datetime"] = start
        properties["end_datetime"] = end

    geometry = request.geometry
    if geometry is None and request.bbox:
        geometry, _ = bbox_to_geometry(request.bbox)

    try:
        obj = pystac.Item(
            id=request.id,
            geometry=geometry,
            bbox=request.bbox,
            datetime=constructor_datetime,
            stac_extensions=request.stac_extensions,
            properties=properties,
            collection=request.collection,
        )
    except pystac.STACError as exc:
        raise ValueError(f"Cannot build STAC Item: {exc}") from exc

    stac_object = obj.to_dict(include_self_link=False, transform_hrefs=False)
    stac_object["stac_version"] = "1.1.0"
    if request.assets:
        stac_object["assets"] = request.assets
    return stac_object


def _validate_iso8601(value: str, field: str) -> None:
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO 8601 timestamp, got {value!r}.") from exc


def _overlay_fields(base: dict[str, Any], request: BuildItemRequest) -> dict[str, Any]:
    """Start from the base object; explicitly provided request fields win."""

    stac_object = dict(base)
    stac_object["type"] = "Feature"

    overrides = {key: value for key, value in request.model_dump(exclude_unset=True).items() if key not in _INTERNAL_FIELDS}

    properties = {**(stac_object.get("properties") or {})}
    for key, value in overrides.items():
        if key == "links":
            continue  # an explicitly empty list must not wipe base links; applied uniformly later
        if key in ("datetime", "start_datetime", "end_datetime", "title", "description", "properties"):
            if value is not None:
                if key == "properties":
                    properties.update(value)
                else:
                    properties[key] = value
        else:
            stac_object[key] = value
    stac_object["properties"] = properties

    # Keep geometry/bbox consistent: derive whichever side is missing.
    if stac_object.get("geometry") is None and stac_object.get("bbox"):
        stac_object["geometry"], _ = bbox_to_geometry(stac_object["bbox"])
    elif stac_object.get("geometry") and stac_object.get("bbox") is None:
        stac_object["bbox"] = _geometry_bbox(stac_object["geometry"])

    if request.stac_extensions:
        stac_object["stac_extensions"] = request.stac_extensions
    stac_object["stac_version"] = "1.1.0"

    return stac_object


def _geometry_bbox(geometry: dict[str, Any]) -> list[float] | None:
    """2D bbox from a GeoJSON polygon-ish geometry's coordinate rings."""

    try:
        coordinates = geometry["coordinates"][0]
        xs = [float(point[0]) for point in coordinates]
        ys = [float(point[1]) for point in coordinates]
        return [min(xs), min(ys), max(xs), max(ys)]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _structural_warnings(request: BuildItemRequest, stac_object: dict[str, Any]) -> list[str]:
    """Spec requirements pystac does not enforce at construction time."""

    warnings: list[str] = []
    rels = {link.get("rel") for link in stac_object.get("links") or []}
    if "root" not in rels:
        warnings.append("item has no root link: required by the STAC spec")
    if not request.datetime and not (request.start_datetime and request.end_datetime) and not (
        stac_object.get("properties") or {}
    ).get("datetime"):
        # Only reachable when overlaying a base whose properties carry no
        # temporal information either; field-built Items fail construction.
        warnings.append("item needs datetime or start_datetime + end_datetime")
    if request.bbox and len(request.bbox) not in (4, 6):
        warnings.append(f"bbox must contain 4 or 6 coordinates, got {len(request.bbox)}")
    if stac_object.get("geometry") and stac_object.get("bbox") is None:
        warnings.append("geometry without bbox: most tools expect both")
    if stac_object.get("geometry") is None and stac_object.get("bbox") is None:
        warnings.append("item has neither geometry nor bbox: required by the STAC spec")
    return warnings


def _export(request: BuildItemRequest, stac_object: dict[str, Any], target: str) -> None:
    payload = json.dumps(stac_object, indent=2)
    if is_s3_uri(target):
        fs, path = s3_url_to_fs(
            target,
            storage_options=resolve_s3_storage_options(_s3_connection(request)),
        )
        if not request.overwrite and fs.exists(path):
            raise FileExistsError(f"Target already exists: {target}. Set overwrite=true to replace it.")
        fs.write_text(path, payload)
    else:
        path = Path(target)
        if not request.overwrite and path.exists():
            raise FileExistsError(f"Target already exists: {target}. Set overwrite=true to replace it.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
    logger.info("Exported STAC object target=%s", target)
