"""NetCDF source opening, download, and metadata extraction (xarray).

Metadata extraction (dimensions, coords, variables, extents) is
format-agnostic and lives in :mod:`ept.infrastructure.formats.xarray`.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import fsspec
import xarray as xr

from ept.infrastructure.formats.xarray import DatasetSummary, summarize_dataset
from ept.infrastructure.storage.s3 import is_http_url, is_s3_uri

logger = logging.getLogger(__name__)

# OPeNDAP/DAP endpoints, which only the netCDF4 engine can open. Matched
# against the lowercased URL path; plain .nc-over-HTTP URLs go through
# fsspec (h5netcdf) with random access instead.
_DAP_MARKERS = ("/dodsc/", "/griddap/", "/tabledap/", "/sosc/")
_NETCDF_SUFFIXES = (".nc", ".nc4", ".cdf")


def is_dap_source(uri: str) -> bool:
    """True for OPeNDAP/DAP endpoints, whose responses are DAP-encoded rather than NetCDF bytes.

    THREDDS ``dodsC`` URLs keep the dataset's real name (usually ``.nc``) yet
    are always DAP endpoints — they serve no raw file. ERDDAP ``griddap``
    ``.nc`` URLs, in contrast, are full-file HTTP downloads.
    """

    path = urlparse(uri).path.lower()
    if "/dodsc/" in path:
        return True
    return any(marker in path for marker in _DAP_MARKERS) and not path.endswith(_NETCDF_SUFFIXES)


def thredds_file_server_url(uri: str) -> str | None:
    """Return the THREDDS ``fileServer`` mirror of a ``dodsC`` URL, else ``None``.

    THREDDS serves the same dataset file raw under ``fileServer`` with the
    identical path. Bulk DAP reads of large variables get rejected by servers
    (``NetCDF: Authorization failure``), so full-read workloads download the
    file via ``fileServer`` when available; non-THREDDS DAP endpoints return
    ``None`` and keep the DAP path.
    """

    if not is_http_url(uri):
        return None
    rewritten, count = re.subn(r"/dodsc/", "/fileServer/", uri, count=1, flags=re.IGNORECASE)
    return rewritten if count else None


def open_netcdf(
    uri: str,
    *,
    engine: str | None = None,
    chunks: int | dict[str, int] | str | None = None,
    decode_cf: bool | None = None,
    mask_and_scale: bool | None = None,
    storage_options: dict[str, Any] | None = None,
) -> xr.Dataset:
    """Open a local, ``s3://``, HTTP(S), or OPeNDAP/DAP NetCDF resource as an xarray Dataset.

    ``s3://`` goes through fsspec with the ``h5netcdf`` engine (honouring
    request-scoped ``storage_options``). OPeNDAP/DAP URLs use the ``netcdf4``
    engine, which speaks DAP natively; other HTTP(S) URLs use fsspec+h5netcdf
    with random access. Full-read workloads (e.g. NetCDF→Zarr) should call
    :func:`download_netcdf` first and open the returned local path.
    """
    uri = os.fspath(uri)
    lower = uri.lower()
    # Local files need no scheme check: cached downloads (download_netcdf) are
    # hash-named without a NetCDF suffix, and xarray validates the content itself.
    if (
        not lower.startswith(("http://", "https://", "s3://"))
        and not lower.endswith(_NETCDF_SUFFIXES)
        and not os.path.isfile(uri)
    ):
        raise ValueError(
            "uri must point to a NetCDF file or use the http://, https://, or s3:// scheme."
        )

    open_kwargs: dict[str, Any] = {}
    for key, value in (
        ("engine", engine),
        ("chunks", chunks),
        ("decode_cf", decode_cf),
        ("mask_and_scale", mask_and_scale),
    ):
        if value is not None:
            open_kwargs[key] = value

    if is_s3_uri(uri):
        return xr.open_dataset(
            uri,
            engine=engine or "h5netcdf",
            storage_options=storage_options or {},
            **{k: v for k, v in open_kwargs.items() if k != "engine"},
        )
    if is_http_url(uri):
        open_kwargs.setdefault("engine", "netcdf4" if is_dap_source(uri) else "h5netcdf")
        return xr.open_dataset(uri, **open_kwargs)
    return xr.open_dataset(uri, **open_kwargs)


def download_netcdf(uri: str, *, storage_options: dict[str, Any] | None = None) -> str:
    """Download a remote NetCDF once into a local cache and return the path.

    Full-read workloads (nc → zarr) read every byte of the source, so one
    sequential GET beats block-cache range requests over HTTP (latency-bound
    per request, and HDF5 seeks the file non-linearly).
    honey: simplecache keys on URL only; a changed remote file is served stale
    until the cache dir is cleared. Key by etag if sources mutate.
    """
    protocol = "https" if uri.startswith("http") else urlparse(uri).scheme
    local = fsspec.open_local(
        f"simplecache::{uri}",
        simplecache={"cache_storage": os.environ.get("EPT_NC_CACHE", "/tmp/ept-nc-cache")},
        **{protocol: dict(storage_options or {})},
    )
    logger.info("Cached remote NetCDF source uri=%s path=%s", uri, local)
    return local


def summarize_netcdf(
    source_uri: str,
    *,
    storage_options: dict[str, Any] | None = None,
    decode_cf: bool = True,
) -> DatasetSummary:
    """Open one NetCDF source, extract its metadata, and close the dataset."""
    try:
        with open_netcdf(
            source_uri, storage_options=storage_options, decode_cf=decode_cf
        ) as dataset:
            return _summarized(dataset, source_uri)
    except Exception as exc:
        if not (is_http_url(source_uri) and not is_dap_source(source_uri)):
            raise
        logger.info("Random-access open failed (%s), downloading source=%s", exc, source_uri)
    # netCDF-3 classic files cannot be read by h5netcdf — download once and
    # let xarray pick a local engine.
    with open_netcdf(download_netcdf(source_uri), decode_cf=decode_cf) as dataset:
        return _summarized(dataset, source_uri)


def _summarized(dataset: xr.Dataset, source_uri: str) -> DatasetSummary:
    summary = summarize_dataset(dataset, source=source_uri)
    logger.info(
        "Summarized NetCDF source=%s dims=%d coords=%d variables=%d bbox=%s",
        source_uri,
        len(summary.dims),
        len(summary.coords),
        len(summary.variables),
        summary.bbox is not None,
    )
    return summary
