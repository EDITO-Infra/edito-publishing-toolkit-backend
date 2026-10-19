"""Unit tests: NetCDF metadata extraction, STAC item building, collection matching."""

from datetime import UTC, datetime

import numpy as np
import pytest
import xarray as xr

from ept.features.netcdf_to_stac.adapters import (
    default_item_id,
    find_matching_collection,
    item_properties,
    suggested_collection_id,
    write_item,
)
from ept.infrastructure.formats.netcdf import summarize_dataset
from ept.infrastructure.formats.stac import item as stac_item
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
                    "long_name": "Sea water potential temperature",
                },
            ),
        },
        coords={
            "time": ("time", np.array(["2025-01-01", "2025-01-02"], dtype="datetime64[ns]")),
            "lat": ("lat", np.array([50.0, 52.0]), {"standard_name": "latitude", "units": "degrees_north"}),
            "lon": ("lon", np.array([2.0, 4.0]), {"standard_name": "longitude", "units": "degrees_east"}),
        },
        attrs={
            "Conventions": "CF-1.10",
            "title": "Sample ocean reanalysis",
            "institution": "VLIZ",
        },
    )


@unit
def test_summarize_extracts_dims_coords_variables_and_attrs():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")

    assert summary.dims == {"time": 2, "lat": 2, "lon": 2}
    assert set(summary.coords) == {"time", "lat", "lon"}
    assert set(summary.variables) == {"thetao"}
    assert summary.variables["thetao"].standard_name == "sea_water_potential_temperature"
    assert summary.variables["thetao"].units == "degree_Celsius"
    assert summary.attrs["Conventions"] == "CF-1.10"


@unit
def test_summarize_derives_bbox_and_temporal_from_coordinates():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")

    assert summary.bbox == [2.0, 50.0, 4.0, 52.0]
    assert summary.temporal == (
        datetime(2025, 1, 1),
        datetime(2025, 1, 2),
    )
    assert summary.standard_names == [
        "sea_water_potential_temperature",
        "latitude",
        "longitude",
    ]


@unit
def test_summarize_falls_back_to_temp_download_for_http_sources(tmp_path, monkeypatch):
    """netCDF-3-style HTTP sources: h5netcdf fails, temp download path opens it."""
    from ept.infrastructure.formats.netcdf import read

    downloaded = tmp_path / "downloaded.nc"
    _sample_dataset().to_netcdf(downloaded)
    real_open_dataset = xr.open_dataset

    def fake_open_dataset(path, **kwargs):
        if kwargs.get("engine") == "h5netcdf":
            raise OSError("not HDF5")
        return real_open_dataset(path, **kwargs)

    monkeypatch.setattr(read.xr, "open_dataset", fake_open_dataset)
    monkeypatch.setattr(read, "download_netcdf", lambda _url, **_kw: downloaded)

    summary = read.summarize_netcdf("https://erddap.example.org/files/ds/data.nc")

    assert list(summary.variables) == ["thetao"]
    assert summary.bbox == [2.0, 50.0, 4.0, 52.0]


@unit
def test_open_netcdf_routes_dap_endpoints_to_netcdf4_engine(monkeypatch):
    from ept.infrastructure.formats.netcdf import read

    engines = []

    def fake_open_dataset(path, **kwargs):
        engines.append(kwargs.get("engine"))
        return xr.Dataset()

    monkeypatch.setattr(read.xr, "open_dataset", fake_open_dataset)
    read.open_netcdf("https://erddap.example.org/erddap/griddap/some_id")
    read.open_netcdf("https://example.org/files/data.nc")

    assert engines == ["netcdf4", "h5netcdf"]


@unit
def test_summarize_falls_back_to_cf_global_attributes():
    dataset = xr.Dataset(
        {"sst": (("x",), np.zeros(2))},
        coords={"x": np.arange(2)},
        attrs={
            "geospatial_lat_min": "48.0",
            "geospatial_lat_max": "49.0",
            "geospatial_lon_min": "-6.0",
            "geospatial_lon_max": "-5.0",
            "time_coverage_start": "2024-06-01T00:00:00Z",
            "time_coverage_end": "2024-06-30T00:00:00Z",
        },
    )

    summary = summarize_dataset(dataset, source="attrs.nc")

    assert summary.bbox == [-6.0, 48.0, -5.0, 49.0]
    assert summary.temporal[0] == datetime(2024, 6, 1, tzinfo=UTC)


@unit
def test_summarize_extracts_instant_from_date_attributes():
    """No time coordinate/coverage attrs: ACDD date attrs yield the STAC instant."""
    dataset = xr.Dataset(
        {"sst": (("lat", "lon"), np.zeros((2, 2)))},
        coords={
            "lat": ("lat", np.array([50.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([2.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"date_created": "2024-03-15T10:30:00Z"},
    )

    summary = summarize_dataset(dataset, source="climatology.nc")

    assert summary.temporal is None
    assert summary.instant == datetime(2024, 3, 15, 10, 30, tzinfo=UTC)
    assert summary.to_dict()["start_datetime"] == "2024-03-15T10:30:00+00:00"


@unit
def test_summarize_instant_survives_unparseable_date_attributes():
    dataset = xr.Dataset(
        {"sst": (("x",), np.zeros(2))},
        attrs={"creation_date": "March 2024, sometime", "date_modified": "2024-03-01"},
    )

    summary = summarize_dataset(dataset, source="odd.nc")

    assert summary.instant == datetime(2024, 3, 1)


@unit
def test_build_item_core_validates_and_carries_collection():
    geometry, bbox = stac_item.bbox_to_geometry([2.0, 50.0, 4.0, 52.0])
    payload = stac_item.build_item(
        stac_item.StacItemInput(
            id="sample-ocean",
            geometry=geometry,
            bbox=bbox,
            collection="projects/x/temperature",
            properties={
                "Conventions": "CF-1.10",
                "start_datetime": "2025-01-01T00:00:00+00:00",
                "end_datetime": "2025-01-02T00:00:00+00:00",
            },
            assets={
                "data": stac_item.StacAssetInput(
                    href="s3://bucket/data.nc",
                    type="application/x-netcdf",
                    roles=["data"],
                )
            },
            links=[
                stac_item.StacLinkInput(
                    href="https://api.dive.edito.eu/data/collections/projects%2Fx%2Ftemperature",
                    rel="collection",
                    type="application/json",
                )
            ],
        )
    )
    stac_item.validate_item(payload)

    assert payload["stac_version"] == "1.1.0"
    assert payload["collection"] == "projects/x/temperature"
    assert payload["bbox"] == [2.0, 50.0, 4.0, 52.0]
    assert payload["properties"]["start_datetime"].startswith("2025-01-01T00:00:00")
    assert payload["properties"]["end_datetime"].startswith("2025-01-02T00:00:00")
    assert payload["assets"]["data"]["href"] == "s3://bucket/data.nc"
    assert payload["assets"]["data"]["roles"] == ["data"]
    assert any(
        link["rel"] == "collection" and "projects%2Fx%2Ftemperature" in link["href"]
        for link in payload["links"]
    )


@unit
def test_build_item_supports_single_datetime_instant():
    geometry, bbox = stac_item.bbox_to_geometry([2.0, 50.0, 4.0, 52.0])
    payload = stac_item.build_item(
        stac_item.StacItemInput(
            id="climatology",
            geometry=geometry,
            bbox=bbox,
            properties={"datetime": "2024-03-15T00:00:00+00:00"},
            assets={"data": stac_item.StacAssetInput(href="s3://bucket/data.nc")},
        )
    )

    assert payload["properties"]["datetime"].startswith("2024-03-15T00:00:00")
    assert "start_datetime" not in payload["properties"]
    assert "end_datetime" not in payload["properties"]


@unit
def test_build_item_rejects_invalid_geometry():
    geometry, bbox = stac_item.bbox_to_geometry([999.0, 50.0, 4.0, 52.0])
    payload = stac_item.build_item(
        stac_item.StacItemInput(
            id="bad",
            geometry=geometry,
            bbox=bbox,
            collection="c",
            properties={
                "start_datetime": "2025-01-01T00:00:00+00:00",
                "end_datetime": "2025-01-02T00:00:00+00:00",
            },
            assets={"data": stac_item.StacAssetInput(href="s3://bucket/data.nc")},
        )
    )

    with pytest.raises(ValueError, match="failed schema validation"):
        stac_item.validate_item(payload)


@unit
def test_find_matching_collection_matches_variables_and_standard_names():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")
    collections = [
        {"id": "unrelated", "title": "Bathymetry products"},
        {
            "id": "temperature",
            "keywords": ["sea_water_potential_temperature", "GEOSEAS"],
        },
    ]

    matched = find_matching_collection(summary, collections)

    assert matched["id"] == "temperature"


@unit
def test_find_matching_collection_returns_none_without_match():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")

    assert find_matching_collection(summary, [{"id": "unrelated", "title": "Bathymetry"}]) is None


@unit
def test_suggested_collection_id_derived_from_official_vocabulary():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")

    suggestion = suggested_collection_id(summary)

    # CF standard_name of the data variable, kebab-cased.
    assert suggestion == "sea-water-potential-temperature"


@unit
def test_item_properties_promote_cf_attrs_and_variables():
    summary = summarize_dataset(_sample_dataset(), source="memory.nc")

    properties = item_properties(summary, {"custom": "value"})

    assert properties["Conventions"] == "CF-1.10"
    assert properties["title"] == "Sample ocean reanalysis"
    assert properties["variables"]["thetao"]["units"] == "degree_Celsius"
    assert properties["custom"] == "value"


@unit
def test_default_item_id_from_local_and_remote_sources():
    assert default_item_id("/data/observations-2025.nc") == "observations-2025"
    assert default_item_id("s3://bucket/prefix/obs.nc?x=1") == "obs"
    assert default_item_id("https://erddap.example.org/dodsC/eta5") == "eta5"


@unit
def test_write_item_locally_with_overwrite_guard(tmp_path):
    target = tmp_path / "nested" / "item.json"

    written = write_item({"id": "x"}, str(target))

    assert written == str(target)
    assert target.read_text(encoding="utf-8") == '{\n  "id": "x"\n}'
    with pytest.raises(FileExistsError, match="overwrite=true"):
        write_item({"id": "x"}, str(target), overwrite=False)


# ─── Output target resolution ────────────────────────────────────────────────


def _target_request(**overrides):
    from ept.features.netcdf_to_stac.models import NetcdfToStacItemRequest

    return NetcdfToStacItemRequest.model_validate(
        {
            "source_uri": "s3://bucket/data.nc",
            "target": "stac/items",
            "endpoint_url": "https://s3.example.test",
            "access_key_id": "key",
            "secret_access_key": "secret",
            **overrides,
        }
    )


@unit
def test_resolve_target_uri_appends_item_id_to_prefix():
    from ept.features.netcdf_to_stac.service import resolve_target_uri

    target_uri, is_s3 = resolve_target_uri(_target_request(item_id="observations"), "oidc-user")

    assert target_uri == "s3://oidc-user/stac/items/observations.json"
    assert is_s3


@unit
def test_resolve_target_uri_target_ending_in_json_overrides_file_name():
    from ept.features.netcdf_to_stac.service import resolve_target_uri

    request = _target_request(item_id="observations", target="stac/custom.json", bucket="proj")

    target_uri, _ = resolve_target_uri(request, "oidc-user")

    assert target_uri == "s3://proj/stac/custom.json"


@unit
def test_resolve_target_uri_local_output_directory():
    from ept.features.netcdf_to_stac.service import resolve_target_uri

    request = _target_request(item_id="observations", target=None, local_output="/out/items")

    target_uri, is_s3 = resolve_target_uri(request, "oidc-user")

    assert (target_uri, is_s3) == ("/out/items/observations.json", False)


@unit
def test_resolve_target_uri_requires_a_bucket():
    from ept.features.netcdf_to_stac.service import resolve_target_uri

    with pytest.raises(ValueError, match="bucket is required"):
        resolve_target_uri(_target_request(item_id="x"), None)
