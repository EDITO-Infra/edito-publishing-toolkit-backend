"""Use-case logic for the STAC Collection builder.

Same pipeline as the Item builder (fields / ``json`` base / ``source_url``
base, links rebuilt from the request, optional export) — deliberately
duplicated per type rather than shared, so each feature stays self-contained.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from starlette.concurrency import run_in_threadpool

from ept.infrastructure.formats.stac import resolve_base
from ept.infrastructure.formats.stac.collection import (
    CollectionInput,
    build_collection as build_collection_from_fields,
    validate_collection,
)
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    is_s3_uri,
    resolve_s3_storage_options,
    s3_url_to_fs,
    with_file_name,
)

from .models import BuildCollectionRequest

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

_OBJECT_FILENAME = "collection.json"


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


def _s3_connection(request: BuildCollectionRequest) -> S3ConnectionParameters | None:
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


async def build_collection(
    request: BuildCollectionRequest,
    *,
    http_client: httpx.AsyncClient,
    access_token: str | None = None,
    default_bucket: str | None = None,
) -> dict[str, Any]:
    """Load the base object (``json``/``source_url``), build the Collection, export when ``target`` is set."""

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
        "Built STAC Collection id=%s mode=%s exported=%s target=%s warnings=%d",
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


def _build_object(base: dict[str, Any] | None, request: BuildCollectionRequest) -> dict[str, Any]:
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
    # Schema-required keys; fall back like the fields-mode builder does.
    stac_object.setdefault(
        "extent",
        {"spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]}, "temporal": {"interval": [[None, None]]}},
    )
    stac_object.setdefault("license", "proprietary")
    stac_object.setdefault("description", stac_object.get("id", ""))
    stac_object.update(request.extra_fields)
    validate_collection(stac_object)
    return stac_object


def _build_from_fields(request: BuildCollectionRequest) -> dict[str, Any]:
    return build_collection_from_fields(
        CollectionInput(
            id=request.id,
            title=request.title,
            description=request.description,
            stac_extensions=request.stac_extensions,
            extent=request.extent,
            license=request.license,
            providers=request.providers,
        )
    )


def _overlay_fields(base: dict[str, Any], request: BuildCollectionRequest) -> dict[str, Any]:
    stac_object = dict(base)
    stac_object["type"] = "Collection"

    # `links` never merges from overrides here: an explicitly empty request list
    # must not wipe the base object's links; _build applies
    # request.links (when non-empty) uniformly afterwards.
    overrides = {
        key: value
        for key, value in request.model_dump(exclude_unset=True).items()
        if key not in _INTERNAL_FIELDS and key != "links"
    }
    stac_object.update({key: value for key, value in overrides.items() if value is not None})

    if request.stac_extensions:
        stac_object["stac_extensions"] = request.stac_extensions
    stac_object["stac_version"] = "1.1.0"

    return stac_object


def _structural_warnings(request: BuildCollectionRequest, stac_object: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not request.extent:
        warnings.append("collection without extent: required by the STAC spec (a world-wide placeholder was used)")
    if not request.description:
        warnings.append("collection description fell back to title/id: set an explicit description")
    rels = {link.get("rel") for link in stac_object.get("links") or []}
    if "root" not in rels:
        warnings.append("collection has no root link: required by the STAC spec")
    return warnings


def _export(request: BuildCollectionRequest, stac_object: dict[str, Any], target: str) -> None:
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
