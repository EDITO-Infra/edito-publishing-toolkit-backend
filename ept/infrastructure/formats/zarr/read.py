"""Zarr store opening (xarray): local directories, ``s3://`` prefixes, HTTP(S)."""

from __future__ import annotations

import logging
from typing import Any

import xarray as xr

from ept.infrastructure.formats.xarray import DatasetSummary, summarize_dataset
from ept.infrastructure.storage.s3 import is_s3_uri

logger = logging.getLogger(__name__)


def summarize_zarr(
    source_uri: str,
    *,
    storage_options: dict[str, Any] | None = None,
    decode_cf: bool = True,
    consolidated: bool | None = None,
) -> DatasetSummary:
    """Open one Zarr store, extract its metadata, and close the dataset.

    ``consolidated=None`` (default) lets xarray auto-detect whether the store
    has consolidated metadata and fall back to a bare scan when it does not.
    """
    with open_zarr(
        source_uri,
        storage_options=storage_options,
        decode_cf=decode_cf,
        consolidated=consolidated,
    ) as dataset:
        summary = summarize_dataset(dataset, source=source_uri)
    logger.info(
        "Summarized Zarr source=%s dims=%d coords=%d variables=%d bbox=%s",
        source_uri,
        len(summary.dims),
        len(summary.coords),
        len(summary.variables),
        summary.bbox is not None,
    )
    return summary


def open_zarr(
    source_uri: str,
    *,
    storage_options: dict[str, Any] | None = None,
    decode_cf: bool = True,
    consolidated: bool | None = None,
) -> xr.Dataset:
    """Open a local Zarr store, ``s3://`` prefix, or HTTP(S) URL.

    ``s3://`` honours request-scoped ``storage_options``; other sources use
    fsspec (local filesystem or HTTP). Zarr v2 and v3 stores are both
    auto-detected by zarr-python.
    """
    if is_s3_uri(source_uri):
        return xr.open_zarr(
            source_uri,
            storage_options=storage_options or {},
            decode_cf=decode_cf,
            consolidated=consolidated,
        )
    return xr.open_zarr(source_uri, decode_cf=decode_cf, consolidated=consolidated)
