"""xarray dataset metadata extraction, shared by the format readers.

NetCDF, Zarr, and other xarray-openable sources all reduce to an
:class:`xarray.Dataset`; this module extracts from it everything a STAC Item
needs: dimensions, coordinates, data variables, global and variable
attributes, and the derived spatial and temporal extents (CF conventions,
https://cfconventions.org). Format readers (netcdf, zarr) open their source
and delegate here.
"""

from ept.infrastructure.formats.xarray.read import (
    DatasetSummary,
    VariableSummary,
    summarize_dataset,
)

__all__ = [
    "DatasetSummary",
    "VariableSummary",
    "summarize_dataset",
]
