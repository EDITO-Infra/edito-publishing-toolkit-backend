"""NetCDF to Zarr conversion use case."""

import logging
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from ept.infrastructure.formats.netcdf import (
    download_netcdf,
    is_dap_source,
    open_netcdf,
    thredds_file_server_url,
)
from ept.infrastructure.formats.zarr import (
    ZarrWriteOptions,
    create_zarr_store,
    write_zarr,
)
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    full_s3_url,
    is_http_url,
    is_s3_uri,
    resolve_s3_storage_options,
    safe_export_name,
    with_file_name,
)

from .models import NetcdfToZarrRequest, NetcdfToZarrResponse

logger = logging.getLogger(__name__)

_S3_REQUIRED_FIELDS = ("endpoint_url", "access_key_id", "secret_access_key")
_NETCDF_SUFFIXES = (".nc", ".nc4", ".cdf")


def nc_to_zarr(request: NetcdfToZarrRequest, default_bucket: str | None = None) -> NetcdfToZarrResponse:
    """Convert a local, S3, or OpenDAP NetCDF source to a local or S3 Zarr store."""

    store_name = _store_name(request.source_uri, request.output_name)
    if request.local_output:
        bucket = None
        target_uri = with_file_name(request.local_output, store_name)
    else:
        bucket = _resolve_bucket(request, default_bucket)
        target_uri = f"s3://{bucket}/{with_file_name(request.target, store_name)}"

    s3_params = _s3_connection(request)
    source_options = resolve_s3_storage_options(s3_params) if is_s3_uri(request.source_uri) else None
    output_options: dict[str, Any] | None = (
        resolve_s3_storage_options(s3_params) if bucket else None
    )
    logger.info(
        "Starting NetCDF to Zarr conversion source=%s target=%s engine=%s chunks=%s "
        "decode_cf=%s mask_and_scale=%s overwrite=%s zarr_format=%s",
        request.source_uri,
        target_uri,
        request.engine,
        request.chunks,
        request.decode_cf,
        request.mask_and_scale,
        request.overwrite,
        request.zarr_format,
    )
    # Full-read conversion: one sequential GET beats fsspec block cache for
    # whole-file sources. THREDDS DAP endpoints reject bulk reads, so prefer
    # their fileServer mirror; other DAP endpoints open lazily via the
    # netcdf4 engine (DAP-native).
    source = request.source_uri
    if is_s3_uri(source) or (is_http_url(source) and not is_dap_source(source)):
        source = download_netcdf(source, storage_options=source_options)
    else:
        file_server_url = thredds_file_server_url(source)
        if file_server_url:
            try:
                source = download_netcdf(file_server_url, storage_options=source_options)
            except Exception as exc:
                logger.info(
                    "THREDDS fileServer download failed (%s); opening DAP source directly %s",
                    exc,
                    request.source_uri,
                )
    dataset = open_netcdf(
        source,
        engine=request.engine,
        chunks=request.chunks,
        decode_cf=request.decode_cf,
        mask_and_scale=request.mask_and_scale,
    )
    try:
        logger.info(
            "Opened NetCDF source dimensions=%s data_variables=%s coordinates=%s",
            dict(dataset.sizes),
            list(dataset.data_vars),
            list(dataset.coords),
        )
        create_zarr_store(target_uri)
        result = write_zarr(
            dataset,
            target_uri,
            target_uri=target_uri,
            options=ZarrWriteOptions(
                consolidated=request.consolidated,
                overwrite=request.overwrite,
                zarr_format=request.zarr_format,
            ),
            storage_options=output_options,
        )
    finally:
        close_dataset = getattr(dataset, "close", None)
        if callable(close_dataset):
            close_dataset()

    logger.info("Successfully converted NetCDF from %s to Zarr at %s", request.source_uri, result.target_uri)

    if bucket:
        result_uri = full_s3_url(
            (output_options or {}).get("endpoint_url"), result.target_uri.removeprefix("s3://")
        )
    else:
        result_uri = result.target_uri
    logger.info("NetCDF to Zarr output target=%s", result_uri)

    return NetcdfToZarrResponse(
        source_uri=request.source_uri,
        bucket=bucket,
        target_uri=result_uri,
        status="completed",
    )


def _store_name(source: str, output_name: str | None) -> str:
    """Derive the Zarr store name: source file name without its NetCDF extension, with .zarr."""

    if output_name:
        name = safe_export_name(output_name, fallback="store")
    else:
        if is_http_url(source):
            name = PurePosixPath(unquote(urlsplit(source).path.rstrip("/"))).name
        else:
            name = Path(source.rstrip("/\\")).name
        if not name:
            raise ValueError("source_uri must include a file or dataset name.")
        lower = name.lower()
        for suffix in _NETCDF_SUFFIXES:
            if lower.endswith(suffix):
                name = name[: -len(suffix)]
                break
        name = safe_export_name(name, fallback="store")
    return name if name.lower().endswith(".zarr") else f"{name}.zarr"


def _resolve_bucket(request: NetcdfToZarrRequest, default_bucket: str | None) -> str:
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


def _s3_connection(request: NetcdfToZarrRequest) -> S3ConnectionParameters | None:
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
