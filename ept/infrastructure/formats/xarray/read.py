"""xarray dataset metadata extraction (dimensions, coords, variables, extents)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import numpy as np
import pandas as pd
import xarray as xr

logger = logging.getLogger(__name__)

# CF coordinate identification: common names, standard_names, and units
# (CF sections 4/5; https://cfconventions.org).
_LAT_NAMES = {"lat", "latitude"}
_LON_NAMES = {"lon", "longitude"}
_LAT_STANDARD_NAMES = {"latitude"}
_LON_STANDARD_NAMES = {"longitude"}
_LAT_UNITS = "degrees_north"
_LON_UNITS = "degrees_east"
_TIME_NAMES = {"time"}

# Dataset-level single-date attributes (ACDD,
# https://wiki.esipfed.org/Attribute_Convention_for_Data_Discovery, plus the
# legacy COARDS-era `creation_date`), in preference order. Used as the STAC
# `datetime` instant when the dataset has no temporal range.
_DATE_ATTR_NAMES = (
    "date_created",
    "creation_date",
    "date_issued",
    "publication_date",
    "date_modified",
    "date_metadata_modified",
)


@dataclass(frozen=True)
class VariableSummary:
    """One coordinate or data variable: type info plus CF attributes."""

    name: str
    dtype: str
    dims: list[str]
    attrs: dict[str, str] = field(default_factory=dict)
    standard_name: str | None = None
    long_name: str | None = None
    units: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "dtype": self.dtype, "dims": self.dims, "attrs": self.attrs}
        for key in ("standard_name", "long_name", "units"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out


@dataclass(frozen=True)
class DatasetSummary:
    """Everything a STAC Item needs from one dataset source."""

    source: str
    dims: dict[str, int]
    coords: dict[str, VariableSummary]
    variables: dict[str, VariableSummary]
    attrs: dict[str, str]
    bbox: list[float] | None
    temporal: tuple[datetime, datetime] | None
    instant: datetime | None

    @property
    def standard_names(self) -> list[str]:
        """CF ``standard_name`` of all variables and coordinates that declare one."""
        return [
            var.standard_name
            for var in (*self.variables.values(), *self.coords.values())
            if var.standard_name
        ]

    def to_dict(self) -> dict[str, Any]:
        start = self.temporal[0] if self.temporal else self.instant
        end = self.temporal[1] if self.temporal else self.instant
        return {
            "source": self.source,
            "dims": self.dims,
            "coords": {name: var.to_dict() for name, var in self.coords.items()},
            "variables": {name: var.to_dict() for name, var in self.variables.items()},
            "attrs": self.attrs,
            "bbox": self.bbox,
            "start_datetime": start.isoformat() if start else None,
            "end_datetime": end.isoformat() if end else None,
            "standard_names": self.standard_names,
        }


def summarize_dataset(dataset: xr.Dataset, *, source: str) -> DatasetSummary:
    """Extract dimensions, coordinates, variables, and extents from a dataset."""
    return DatasetSummary(
        source=source,
        dims={str(name): int(size) for name, size in dataset.sizes.items()},
        coords={str(name): _variable(dataset.coords[name], str(name)) for name in dataset.coords},
        variables={
            str(name): _variable(dataset.data_vars[name], str(name)) for name in dataset.data_vars
        },
        attrs=_sanitize_attrs(dataset.attrs),
        bbox=_bbox(dataset),
        temporal=_temporal(dataset),
        instant=_instant(dataset),
    )


def _variable(variable: xr.DataArray, name: str) -> VariableSummary:
    attrs = _sanitize_attrs(variable.attrs)
    return VariableSummary(
        name=name,
        dtype=str(variable.dtype),
        dims=[str(dim) for dim in variable.dims],
        attrs=attrs,
        standard_name=attrs.get("standard_name"),
        long_name=attrs.get("long_name"),
        units=attrs.get("units"),
    )


def _sanitize_attrs(attrs: Any) -> dict[str, str]:
    """Keep scalar attributes as strings; drop arrays and objects."""
    out: dict[str, str] = {}
    for name, value in dict(attrs).items():
        if value is None or isinstance(value, np.ndarray | tuple | list):
            continue
        if isinstance(value, bytes):
            out[str(name)] = value.decode("utf-8", errors="replace").strip("\x00")
            continue
        out[str(name)] = str(value)
    return out


def _bbox(dataset: xr.Dataset) -> list[float] | None:
    """``[west, south, east, north]`` from CF lat/lon coordinates or global attrs."""
    lat = _coordinate_values(dataset, _LAT_NAMES, _LAT_STANDARD_NAMES, _LAT_UNITS)
    lon = _coordinate_values(dataset, _LON_NAMES, _LON_STANDARD_NAMES, _LON_UNITS)
    if lat is not None and lon is not None:
        return [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
    attrs = dataset.attrs
    try:
        return [
            float(attrs["geospatial_lon_min"]),
            float(attrs["geospatial_lat_min"]),
            float(attrs["geospatial_lon_max"]),
            float(attrs["geospatial_lat_max"]),
        ]
    except (KeyError, TypeError, ValueError):
        logger.info("No spatial extent found in coordinates or CF attributes")
        return None


def _coordinate_values(
    dataset: xr.Dataset, names: set[str], standard_names: set[str], units: str
) -> np.ndarray | None:
    """Finite values of the first variable matching name, standard_name, or CF units."""
    for name in dataset.variables:
        attrs = dataset.variables[name].attrs
        if (
            str(name).lower() not in names
            and str(attrs.get("standard_name", "")) not in standard_names
            and str(attrs.get("units", "")) != units
        ):
            continue
        values = np.asarray(dataset[name].values, dtype=float).ravel()
        finite = values[np.isfinite(values)]
        if finite.size:
            return finite
    return None


def _temporal(dataset: xr.Dataset) -> tuple[datetime, datetime] | None:
    """``(start, end)`` from the time coordinate, or CF time_coverage_* attrs."""
    for name in dataset.variables:
        attrs = dataset.variables[name].attrs
        if (
            str(name).lower() in _TIME_NAMES
            or str(attrs.get("standard_name", "")) == "time"
            or str(attrs.get("axis", "")).upper() == "T"
        ):
            values = np.asarray(dataset[name].values).ravel()
            try:
                start = pd.Timestamp(values.min())
                end = pd.Timestamp(values.max())
            except (TypeError, ValueError):
                break  # e.g. non-standard CF calendar (cftime) — fall through to attrs
            if pd.isna(start) or pd.isna(end):
                break  # all-NaT time coordinate — fall through to attrs
            # pd.isna guards NaT, but pyright can't narrow through it.
            return (
                cast(datetime, start.to_pydatetime()),
                cast(datetime, end.to_pydatetime()),
            )
            return start, end
    attrs = dataset.attrs
    try:
        return (
            datetime.fromisoformat(str(attrs["time_coverage_start"])),
            datetime.fromisoformat(str(attrs["time_coverage_end"])),
        )
    except (KeyError, TypeError, ValueError):
        logger.info("No temporal extent found in coordinates or CF attributes")
        return None


def _instant(dataset: xr.Dataset) -> datetime | None:
    """A single dataset date (e.g. ``date_created``) as the STAC ``datetime``."""
    for name in _DATE_ATTR_NAMES:
        if name not in dataset.attrs:
            continue
        try:
            return datetime.fromisoformat(str(dataset.attrs[name]))
        except ValueError:
            logger.info("Unparseable %s attribute: %r", name, dataset.attrs[name])
    return None
