"""ISO19139 → STAC Item conversion orchestration.

Source dispatch (local, http(s), S3) and output writing live here; XML
reading and parsing primitives come from
``ept.infrastructure.formats.iso19139``, item construction from
``ept.infrastructure.formats.stac``, and the ISO→STAC mapping from the
feature adapter. Sync by design — routes run it in the threadpool.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ept.infrastructure.formats.iso19139 import open_isoxml
from ept.infrastructure.formats.stac import build_item, validate_item
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    full_remote_url,
    is_s3_uri,
    resolve_s3_storage_options,
    s3_url_to_fs,
    safe_export_name,
    slugify,
    with_file_name,
)

from .adapter import Iso19139ToStacOptions, iso19139_to_stac_item_input
from .models import IsoToStacItemRequest, IsoToStacItemResponse

logger = logging.getLogger(__name__)

__all__ = ["iso_to_stac_item"]


def iso_to_stac_item(
    request: IsoToStacItemRequest,
    default_bucket: str | None = None,
    *,
    default_username: str | None = None,
) -> IsoToStacItemResponse:
    """Read an ISO19139 record, convert it to a STAC Item, and write the Item JSON.

    Standard output is ``s3://<bucket>/<target>/<item_id>.json``; when
    ``local_output`` is set the JSON is written to that directory on the EPT
    filesystem instead. A derived id (ISO identifier or title slug) is
    namespaced with the caller's username — same scheme as the suggesters.
    """

    source_uri = request.source_uri.strip()
    s3_params = _s3_connection(request)
    storage_options = resolve_s3_storage_options(s3_params) if is_s3_uri(source_uri) else None

    xml = _read_source(source_uri, storage_options)

    options: Iso19139ToStacOptions = request.resolved_options()
    item_input = iso19139_to_stac_item_input(
        xml,
        options=options,
        record_id=request.item_id,
    )
    stac_item = build_item(item_input)
    if request.item_id is None and default_username:
        stac_item["id"] = f"{default_username}-{slugify(str(stac_item['id']), 'item')}"
    validate_item(stac_item)

    file_name = f"{safe_export_name(str(stac_item['id']), fallback='item')}.json"
    payload = json.dumps(stac_item, indent=2, ensure_ascii=False)

    if request.local_output:
        bucket = None
        target_uri = _write_to_local(
            payload,
            with_file_name(request.local_output, file_name),
            overwrite=request.overwrite,
        )
    else:
        bucket = _resolve_bucket(request, default_bucket)
        s3_uri = f"s3://{bucket}/{with_file_name(request.target, file_name)}"
        target_uri = _write_to_s3(
            payload,
            s3_uri,
            overwrite=request.overwrite,
            storage_options=resolve_s3_storage_options(s3_params),
        )

    logger.info(
        "ISO19139 converted item_id=%s target=%s",
        stac_item["id"],
        target_uri,
    )
    return IsoToStacItemResponse(
        item_id=stac_item["id"],
        stac_item=stac_item,
        bucket=bucket,
        target_uri=target_uri,
        status="completed",
    )


def _s3_connection(request: IsoToStacItemRequest) -> S3ConnectionParameters | None:
    """Return explicit connection parameters, or None to use the service-level S3_* environment."""

    endpoint_url = request.endpoint_url
    access_key_id = request.access_key_id
    secret_access_key = request.secret_access_key
    if not (endpoint_url or access_key_id or secret_access_key):
        return None
    if not (endpoint_url and access_key_id and secret_access_key):
        missing = [
            name
            for name, value in (
                ("endpoint_url", endpoint_url),
                ("access_key_id", access_key_id),
                ("secret_access_key", secret_access_key),
            )
            if not value
        ]
        raise ValueError(
            f"Missing S3 connection fields: {', '.join(missing)}. Provide all of "
            "endpoint_url, access_key_id, and secret_access_key, or none of them "
            "to use the service-level S3_* environment configuration."
        )
    return S3ConnectionParameters(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=request.session_token,
    )


def _resolve_bucket(request: IsoToStacItemRequest, default_bucket: str | None) -> str:
    """Use the explicit bucket, else the caller's personal oidc-<username> bucket."""

    if request.bucket:
        return request.bucket
    if default_bucket:
        logger.info("Using default S3 bucket from authenticated user bucket=%s", default_bucket)
        return default_bucket
    raise ValueError(
        "bucket is required: the bearer token carries no username from which to "
        "derive the default oidc-<username> bucket."
    )


def _read_source(source_uri: str, storage_options: dict[str, object] | None) -> str:
    """Read the ISO record: S3 via storage primitives, otherwise ``open_isoxml``."""

    if is_s3_uri(source_uri):
        fs, path = s3_url_to_fs(source_uri, storage_options=storage_options or {})
        with fs.open(path, "r") as handle:
            data = handle.read()
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        logger.info("Read ISO19139 record from S3 uri=%s bytes=%d", source_uri, len(text))
        return text
    return open_isoxml(source_uri)


def _write_to_s3(
    payload: str,
    target_uri: str,
    *,
    overwrite: bool,
    storage_options: dict[str, object],
) -> str:
    fs, path = s3_url_to_fs(target_uri, storage_options=storage_options)
    if not overwrite and fs.exists(path):
        raise FileExistsError(f"Target already exists: {target_uri}. Set overwrite=true to replace it.")
    with fs.open(path, "w") as handle:
        handle.write(payload)
    return full_remote_url(fs, path)


def _write_to_local(
    payload: str,
    destination: str,
    *,
    overwrite: bool,
) -> str:
    destination = Path(destination)
    if destination.is_dir():
        raise IsADirectoryError(f"local_output path is a directory: {destination}")
    if not overwrite and destination.exists():
        raise FileExistsError(f"Target already exists: {destination}. Set overwrite=true to replace it.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload, encoding="utf-8")
    return str(destination)
