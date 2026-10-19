"""ARCO STAC Item use case: read a STAC Item, ARCO-ify its data assets, write it back."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ept.features.arco_stac_item.conversions import (
    CONVERSIONS,
    Conversion,
    detect_conversion,
    resolve_conversion,
)
from ept.features.arco_stac_item.models import (
    ArcoConversionResult,
    ArcoStacItemRequest,
    ArcoStacItemResponse,
)
from ept.infrastructure.services.edito_auth import (
    EditoBearerAuthError,
    validate_bearer_token,
)
from ept.infrastructure.storage.s3 import is_s3_uri, s3_url_to_fs

__all__ = ["arco_stac_item"]

logger = logging.getLogger(__name__)


async def arco_stac_item(
    request: ArcoStacItemRequest, *, access_token: str | None = None
) -> ArcoStacItemResponse:
    """Runner-compatible entry point; the blocking pipeline runs in a thread."""
    bucket = _resolve_bucket(request, access_token)
    return await asyncio.to_thread(_run, request, bucket)


@dataclass(frozen=True)
class _Outputs:
    """Resolved output locations for one pipeline run."""

    bucket: str
    assets_target: str


def _run(request: ArcoStacItemRequest, bucket: str) -> ArcoStacItemResponse:
    timings: dict[str, int] = {}
    item = _timed(timings, "read_item", _read_item, request.source_uri, request)
    item_id = request.item_id or str(item.get("id") or "")
    if not item_id:
        raise ValueError("Item has no id and no item_id override was provided.")
    item["id"] = item_id

    outputs = _Outputs(bucket=bucket, assets_target=_resolve_assets_target(request))
    plans = _plan_conversions(item, request)
    taken = set(item.get("assets") or {})
    results = []
    for key, asset, conversion in plans:
        converted = _timed(timings, f"convert:{key}:{conversion.key}", _convert, request, outputs, key, asset, conversion)
        results.extend(converted)
    if request.overwrite:
        for stale_key in _stale_generated_keys(item, results, taken):
            del item["assets"][stale_key]
            taken.discard(stale_key)
    for result in results:
        result.asset_key = _unique_asset_key(result.asset_key, result.source_asset_key, taken)
        taken.add(result.asset_key)
        item.setdefault("assets", {})[result.asset_key] = {
            "href": result.target_uri,
            "title": _asset_title(result.feature),
            "type": _asset_type(result.feature),
            "roles": ["data"],
        }

    target_uri = request.resolved_item_uri(request.source_uri, item_id)
    _timed(timings, "write_item", _write_item, item, target_uri, request)
    logger.info("arco_stac_item completed item=%s stages_ms=%s", item_id, timings)
    return ArcoStacItemResponse(
        item_id=item_id,
        stac_item=item,
        source_uri=request.source_uri,
        target_uri=target_uri,
        conversions=results,
        stage_timings_ms=timings,
        status="completed",
    )


def _timed(timings: dict[str, int], stage: str, fn, /, *args, **kwargs):
    """Run one pipeline stage and record its wall time in ms."""
    started = time.monotonic()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[stage] = round((time.monotonic() - started) * 1000)


def _resolve_assets_target(request: ArcoStacItemRequest) -> str:
    """Prefix for converted outputs; explicit, else the item target, else the source folder."""
    if request.assets_target:
        return request.assets_target
    if request.item_target:
        return request.item_target
    if is_s3_uri(request.source_uri):
        bucket, _, prefix = request.source_uri[len("s3://"):].partition("/")
        return prefix.rsplit("/", 1)[0] if "/" in prefix else ""
    raise ValueError(
        "assets_target is required: no item_target set and the source is not an s3:// Item."
    )


# --- conversion planning -----------------------------------------------------


def _plan_conversions(
    item: dict[str, Any], request: ArcoStacItemRequest
) -> list[tuple[str, dict[str, Any], Conversion]]:
    """Decide per asset: explicit ``conversions`` mapping wins, else auto-detect."""
    assets = item.get("assets") or {}
    if not isinstance(assets, dict):
        raise ValueError("Item 'assets' must be an object.")
    plans: list[tuple[str, dict[str, Any], Conversion]] = []
    for key, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        conversion = _explicit_conversion(key, asset, request.conversions)
        if conversion is None:
            conversion = detect_conversion(
                href=str(asset.get("href", "")), media_type=str(asset.get("type", ""))
            )
        if conversion is not None:
            plans.append((str(key), asset, conversion))
    return plans


def _explicit_conversion(
    key: str, asset: dict[str, Any], conversions: dict[str, str]
) -> Conversion | None:
    href = str(asset.get("href", ""))
    # Bare mapping names match only the href's file name; a pattern containing
    # "/" matches the full path. Without this, a name like "zip" would also
    # hit a "/zip/" directory segment in a previously generated output's href
    # and re-convert that output as if it were a source.
    path = urlparse(href).path
    name = path.rsplit("/", 1)[-1]
    for pattern, feature in conversions.items():
        haystack = path if "/" in pattern else name
        if pattern != key and pattern not in haystack:
            continue
        conversion = resolve_conversion(feature)
        if conversion is None:
            raise ValueError(
                f"Unknown conversion '{feature}' for asset '{key}'. "
                f"Declared conversions: {', '.join(CONVERSIONS)}."
            )
        return conversion
    return None


def _convert(
    request: ArcoStacItemRequest,
    outputs: _Outputs,
    key: str,
    asset: dict[str, Any],
    conversion: Conversion,
) -> list[ArcoConversionResult]:
    """Run one declared conversion for one asset via the registry.

    Returns one result per output file. Converters that produce multiple
    files (gdb/shp → parquet) report their per-file URLs in an ``outputs``
    response field, while ``target_uri`` is only the directory prefix — an
    asset href must point at the object, not at the prefix.
    """
    href = str(asset.get("href", "")).strip()
    if not href:
        raise ValueError(f"Asset '{key}' has no href to convert.")
    credentials = {
        "endpoint_url": request.endpoint_url,
        "access_key_id": request.access_key_id,
        "secret_access_key": request.secret_access_key,
        "session_token": request.session_token,
    }
    sub_request = conversion.build_request(
        href=href,
        bucket=outputs.bucket,
        target=outputs.assets_target,
        source_key=_safe_name(key),
        s3_prefix=f"s3://{outputs.bucket}/{outputs.assets_target}",
        suffix=_suffix(href),
        credentials=credentials,
        overwrite=request.overwrite,
    )
    response = conversion.service(sub_request)
    file_uris = [str(uri) for uri in getattr(response, "outputs", None) or []]
    if not file_uris:
        file_uris = [str(response.target_uri)]
    layers = [
        str(layer)
        for layer in (getattr(response, conversion.result_field) if conversion.result_field else None) or []
    ]
    asset_key = conversion.asset_key.format(source=_safe_name(key))
    return [
        ArcoConversionResult(
            source_asset_key=key,
            feature=conversion.key,
            asset_key=asset_key,
            target_uri=file_uri,
            layers=_layer_for(file_uri, layers),
        )
        for file_uri in file_uris
    ]


def _stale_generated_keys(
    item: dict[str, Any], results: list[ArcoConversionResult], taken: set[str]
) -> list[str]:
    """Existing asset keys a previous run generated for the same conversions.

    Keys equal to a declared conversion asset key, or numbered variants of it
    (``parquet``, ``parquet-2``, ...), only ever come from ARCO itself, so an
    ``overwrite`` re-run replaces them instead of stacking new suffixes.
    """
    assets = item.get("assets") or {}
    bases = {result.asset_key for result in results}
    stale = []
    for key in taken:
        if key not in assets:
            continue
        if key in bases:
            stale.append(key)
            continue
        base, _, number = key.rpartition("-")
        if number.isdigit() and base in bases:
            stale.append(key)
    return sorted(stale)


def _layer_for(file_uri: str, layers: list[str]) -> list[str] | None:
    """The layer this output file belongs to, matched on the file stem."""
    if not layers:
        return None
    name = file_uri.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    return [stem] if stem in layers else None


def _unique_asset_key(key: str, source_key: str, taken: set[str]) -> str:
    """Keep the declared asset key; number collisions (-2, -3, ...).

    An upload re-points its own source asset key, so a collision with the
    source key is intentional and kept.
    """
    if key not in taken or key == source_key:
        return key
    n = 2
    while f"{key}-{n}" in taken:
        n += 1
    return f"{key}-{n}"


def _asset_title(feature: str) -> str:
    if feature == "nc-to-zarr":
        return "ARCO Zarr store"
    if feature == "gdb-to-parquet":
        return "GeoParquet"
    if feature == "shp-to-parquet":
        return "GeoParquet"
    return "Hosted copy"


def _asset_type(feature: str) -> str:
    conversion = CONVERSIONS.get(feature)
    return conversion.media_type if conversion else "application/octet-stream"


def _suffix(href: str) -> str:
    name = href.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return Path(name).suffix or ".zip"


def _safe_name(value: str) -> str:
    cleaned = "".join("-" if not c.isalnum() and c not in "._" else c for c in value.strip())
    return cleaned.strip("-") or "asset"


# --- item I/O ----------------------------------------------------------------


def _read_item(source_uri: str, request: ArcoStacItemRequest) -> dict[str, Any]:
    """Read the STAC Item JSON from a local path, s3:// URI, or HTTP(S) URL."""
    if is_s3_uri(source_uri):
        fs, path = s3_url_to_fs(source_uri, storage_options=_storage_options(request))
        with fs.open(path, "r") as handle:
            data = handle.read()
    elif source_uri.lower().startswith(("http://", "https://")):
        response = httpx.get(source_uri, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        data = response.content
    else:
        data = Path(source_uri).read_bytes()
    item = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    if not isinstance(item, dict) or item.get("type") != "Feature":
        raise ValueError(f"source_uri does not contain a STAC Item (type 'Feature'): {source_uri}")
    return item


def _storage_options(request: ArcoStacItemRequest) -> dict[str, str]:
    """Request-scoped options, or {} to let s3fs fall back to the S3_* environment."""
    if request.endpoint_url and request.access_key_id and request.secret_access_key:
        return request.storage_options()
    return {}


def _write_item(item: dict[str, Any], target_uri: str, request: ArcoStacItemRequest) -> None:
    if not is_s3_uri(target_uri):
        raise ValueError("Only s3:// targets are supported for the updated Item JSON.")
    fs, path = s3_url_to_fs(target_uri, storage_options=_storage_options(request))
    if not request.overwrite and fs.exists(path):
        raise FileExistsError(f"Target already exists: {target_uri}. Set overwrite=true to replace it.")
    with fs.open(path, "w") as handle:
        handle.write(json.dumps(item, indent=2))


def _resolve_bucket(request: ArcoStacItemRequest, access_token: str | None) -> str:
    if request.bucket:
        return request.bucket
    if access_token:
        try:
            username = validate_bearer_token(access_token).username
        except EditoBearerAuthError as exc:
            raise ValueError(
                "The bearer token could not be verified to derive the default bucket; pass bucket explicitly."
            ) from exc
        if username:
            return f"oidc-{username}"
    raise ValueError(
        "bucket is required: the bearer token carries no username from which to "
        "derive the default oidc-<username> bucket."
    )
