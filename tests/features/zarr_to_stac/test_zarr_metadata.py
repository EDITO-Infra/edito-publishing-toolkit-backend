"""Unit tests: Zarr metadata extraction, STAC item building, collection suggestion."""

from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from ept.features.zarr_to_stac.adapters import (
    build_item,
    default_item_id,
    find_matching_collection,
    suggested_collection_id,
)
from ept.infrastructure.formats.xarray import summarize_dataset
from tests.conftest import unit


def _sample_dataset() -> xr.Dataset:
    """One time/lat/lon gridded dataset with CF conventions and attrs."""
    return xr.Dataset(
        data_vars={
            "thetao": (
                ("time", "lat", "lon"),
                np.full((2, 2, 2), 12.5),
                {
                    "standard_name": "sea_water_potential_temperature",
                    "units": "degree_Celsius",
                },
            ),
        },
        coords={
            "time": ("time", np.array(["2025-01-01", "2025-01-02"], dtype="datetime64[ns]")),
            "lat": ("lat", np.array([50.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([2.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"Conventions": "CF-1.10", "title": "Sample zarr store"},
    )


def _request(tmp_path, source, **overrides):
    from ept.features.zarr_to_stac.models import ZarrToStacItemRequest

    return ZarrToStacItemRequest.model_validate(
        {
            "source_uri": source,
            "local_output": str(tmp_path),
            "endpoint_url": "https://s3.example.test",
            "access_key_id": "key",
            "secret_access_key": "secret",
            **overrides,
        }
    )


def _write_store(dataset: xr.Dataset, path) -> str:
    dataset.to_zarr(path)
    return str(path)


@unit
def test_summarize_extracts_extents_from_coordinates(tmp_path):
    from ept.infrastructure.formats.zarr import summarize_zarr

    source = _write_store(_sample_dataset(), tmp_path / "store.zarr")

    summary = summarize_zarr(source)

    assert summary.bbox == [2.0, 50.0, 4.0, 52.0]
    assert summary.temporal == (datetime(2025, 1, 1), datetime(2025, 1, 2))
    assert summary.instant is None
    assert summary.variables["thetao"].standard_name == "sea_water_potential_temperature"


@unit
def test_summarize_extracts_instant_from_date_attributes(tmp_path):
    from ept.infrastructure.formats.zarr import summarize_zarr

    dataset = xr.Dataset(
        {"sst": (("lat", "lon"), np.zeros((2, 2)))},
        coords={
            "lat": ("lat", np.array([50.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([2.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"date_created": "2024-03-15T10:30:00Z"},
    )
    source = _write_store(dataset, tmp_path / "store.zarr")

    summary = summarize_zarr(source)

    assert summary.temporal is None
    assert summary.instant == datetime(2024, 3, 15, 10, 30, tzinfo=UTC)


@unit
def test_build_item_single_datetime_from_instant(tmp_path):
    from ept.infrastructure.formats.xarray import DatasetSummary

    summary = DatasetSummary(
        source="store.zarr",
        dims={},
        coords={},
        variables={},
        attrs={},
        bbox=[2.0, 50.0, 4.0, 52.0],
        temporal=None,
        instant=datetime(2024, 3, 15, tzinfo=UTC),
    )

    item = build_item(_request(tmp_path, "s3://bucket/store.zarr"), summary, None)

    assert item["properties"]["datetime"].startswith("2024-03-15T00:00:00")
    assert "start_datetime" not in item["properties"]
    assert item["assets"]["data"]["type"] == "application/x-zarr"


@unit
def test_build_item_requires_temporal_override_when_atemporal(tmp_path):
    from ept.infrastructure.formats.xarray import DatasetSummary

    summary = DatasetSummary(
        source="store.zarr",
        dims={},
        coords={},
        variables={},
        attrs={},
        bbox=[2.0, 50.0, 4.0, 52.0],
        temporal=None,
        instant=None,
    )

    with pytest.raises(ValueError, match="start_datetime.*end_datetime"):
        build_item(_request(tmp_path, "s3://bucket/store.zarr"), summary, None)


@unit
def test_suggested_collection_id_and_matching():
    summary = summarize_dataset(_sample_dataset(), source="memory.zarr")
    collections = [
        {"id": "unrelated", "title": "Bathymetry products"},
        {"id": "temperature", "keywords": ["sea_water_potential_temperature"]},
    ]

    assert suggested_collection_id(summary) == "sea-water-potential-temperature"
    matched = find_matching_collection(summary, collections)
    assert matched is not None and matched["id"] == "temperature"
    assert find_matching_collection(summary, [{"id": "unrelated"}]) is None


@unit
def test_default_item_id_from_store_paths():
    assert default_item_id("/data/store.zarr") == "store"
    assert default_item_id("s3://bucket/prefix/store.zarr/") == "store"
    assert default_item_id("https://example.org/stores/store.zarr") == "store"


# ─── Output target resolution ────────────────────────────────────────────────


def _target_request(**overrides):
    from ept.features.zarr_to_stac.models import ZarrToStacItemRequest

    return ZarrToStacItemRequest.model_validate(
        {
            "source_uri": "s3://bucket/store.zarr",
            "target": "stac/items",
            "endpoint_url": "https://s3.example.test",
            "access_key_id": "key",
            "secret_access_key": "secret",
            **overrides,
        }
    )


@unit
def test_resolve_target_uri_appends_item_id_to_prefix():
    from ept.features.zarr_to_stac.service import resolve_target_uri

    target_uri, is_s3 = resolve_target_uri(_target_request(item_id="observations"), "oidc-user")

    assert target_uri == "s3://oidc-user/stac/items/observations.json"
    assert is_s3


@unit
def test_resolve_target_uri_target_ending_in_json_overrides_file_name():
    from ept.features.zarr_to_stac.service import resolve_target_uri

    request = _target_request(item_id="observations", target="stac/custom.json", bucket="proj")

    target_uri, _ = resolve_target_uri(request, "oidc-user")

    assert target_uri == "s3://proj/stac/custom.json"


@unit
def test_resolve_target_uri_local_output_directory():
    from ept.features.zarr_to_stac.service import resolve_target_uri

    request = _target_request(item_id="observations", target=None, local_output="/out/items")

    target_uri, is_s3 = resolve_target_uri(request, "oidc-user")

    assert (target_uri, is_s3) == ("/out/items/observations.json", False)


@unit
def test_resolve_target_uri_requires_a_bucket():
    from ept.features.zarr_to_stac.service import resolve_target_uri

    with pytest.raises(ValueError, match="bucket is required"):
        resolve_target_uri(_target_request(item_id="x"), None)
