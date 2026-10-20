"""Zarr format primitives: store opening, summarizing, and writing."""

from ept.infrastructure.formats.xarray import DatasetSummary, VariableSummary
from ept.infrastructure.formats.zarr.read import open_zarr, summarize_zarr
from ept.infrastructure.formats.zarr.write import (
    ZarrWriteOptions,
    ZarrWriteResult,
    create_zarr_store,
    write_zarr,
)

__all__ = [
    "DatasetSummary",
    "VariableSummary",
    "ZarrWriteOptions",
    "ZarrWriteResult",
    "create_zarr_store",
    "open_zarr",
    "summarize_zarr",
    "write_zarr",
]
