"""NetCDF format primitives: opening, downloading, and summarizing."""

from ept.infrastructure.formats.netcdf.read import (
    download_netcdf,
    is_dap_source,
    open_netcdf,
    summarize_netcdf,
    thredds_file_server_url,
)
from ept.infrastructure.formats.xarray import DatasetSummary, VariableSummary, summarize_dataset

__all__ = [
    "DatasetSummary",
    "VariableSummary",
    "download_netcdf",
    "is_dap_source",
    "open_netcdf",
    "summarize_dataset",
    "summarize_netcdf",
    "thredds_file_server_url",
]
