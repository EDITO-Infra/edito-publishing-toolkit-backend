"""Integration test: end-to-end NetCDF → STAC Item with a mocked EDITO STAC API."""

import asyncio
import json

import httpx
import numpy as np
import pytest
import xarray as xr

from ept.features.netcdf_to_stac.models import NetcdfToStacItemRequest
from ept.features.netcdf_to_stac.service import netcdf_to_stac_item
from tests.conftest import integration


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand-in for the EDITO STAC API /collections endpoint."""
    if request.url.path.endswith("/collections"):
        return httpx.Response(
            200,
            json={
                "collections": [
                    {"id": "bathymetry", "title": "Bathymetry products"},
                    {
                        "id": "temperature",
                        "keywords": ["sea_water_potential_temperature"],
                    },
                ],
                "links": [],
            },
        )
    return httpx.Response(404, json={"detail": "not found"})


def _request(tmp_path, **overrides) -> NetcdfToStacItemRequest:
    fields = {
        "source_uri": str(tmp_path / "observations.nc"),
        "local_output": str(tmp_path),
        "endpoint_url": "https://s3.example.test",
        "access_key_id": "key",
        "secret_access_key": "secret",
        **overrides,
    }
    return NetcdfToStacItemRequest.model_validate(fields)


@pytest.fixture
def netcdf_file(tmp_path):
    path = tmp_path / "observations.nc"
    xr.Dataset(
        data_vars={
            "thetao": (
                ("time", "lat", "lon"),
                np.full((1, 2, 2), 12.5),
                {"standard_name": "sea_water_potential_temperature", "units": "degree_Celsius"},
            ),
        },
        coords={
            "time": ("time", np.array(["2025-03-01"], dtype="datetime64[ns]")),
            "lat": ("lat", np.array([51.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([3.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"Conventions": "CF-1.10", "title": "Integration sample"},
    ).to_netcdf(path)
    return path


@pytest.fixture
def atemporal_netcdf_file(tmp_path):
    """Climatology-style dataset: spatial extent but no time coordinate."""
    path = tmp_path / "climatology.nc"
    xr.Dataset(
        data_vars={
            "habitat_suitability": (
                ("lat", "lon"),
                np.full((2, 2), 0.5),
                {"standard_name": "sea_water_potential_temperature"},
            ),
        },
        coords={
            "lat": ("lat", np.array([51.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([3.0, 4.0]), {"units": "degrees_east"}),
        },
    ).to_netcdf(path)
    return path


@integration
def test_service_suggests_but_never_attaches_collection(tmp_path, netcdf_file):
    """No collection_id → standalone item; the match is returned as a suggestion only."""
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path), access_token="token", http_client=client
            )

    response = asyncio.run(run())

    assert response.collection_id is None
    assert response.collection_source == "standalone"
    assert response.suggested_collection_id == "temperature"
    assert "collection" not in response.stac_item
    assert response.stac_item["properties"]["Conventions"] == "CF-1.10"
    assert response.dataset.bbox == [3.0, 51.0, 4.0, 52.0]

    # local_output is a directory: the file name is <item_id>.json.
    on_disk = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))
    assert on_disk == response.stac_item


@integration
def test_service_suggests_vocabulary_id_when_nothing_matches(tmp_path, netcdf_file):
    def no_match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"collections": [{"id": "bathymetry"}], "links": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_match)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path), access_token="token", http_client=client
            )

    response = asyncio.run(run())

    assert response.collection_id is None
    assert response.collection_source == "standalone"
    assert response.suggested_collection_id == "sea-water-potential-temperature"
    assert "collection" not in response.stac_item

    on_disk = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))
    assert on_disk == response.stac_item


@integration
def test_service_uses_explicit_collection_without_existence_check(tmp_path, netcdf_file):
    """A collection_id is attached as-is: no EDITO STAC API lookup, published
    or not."""

    def no_api(request: httpx.Request) -> httpx.Response:
        raise AssertionError("collection_id must not trigger an EDITO STAC API call")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_api)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path, collection_id="my-own-collection"),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.collection_id == "my-own-collection"
    assert response.collection_source == "provided"
    assert response.stac_item["collection"] == "my-own-collection"


@integration
def test_service_applies_temporal_override_for_atemporal_dataset(tmp_path, atemporal_netcdf_file):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await netcdf_to_stac_item(
                _request(
                    tmp_path,
                    source_uri=str(atemporal_netcdf_file),
                    start_datetime="2000-01-01T00:00:00Z",
                    end_datetime="2000-12-31T23:59:59Z",
                ),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.stac_item["properties"]["start_datetime"] == "2000-01-01T00:00:00+00:00"
    assert response.stac_item["properties"]["end_datetime"] == "2000-12-31T23:59:59+00:00"


@integration
def test_service_uses_date_attribute_instant_for_atemporal_dataset(
    tmp_path, atemporal_netcdf_file
):
    """No time coordinate, but a `date_created` attr → single STAC datetime."""
    xr.Dataset(
        {"habitat_suitability": (("lat", "lon"), np.full((2, 2), 0.5))},
        coords={
            "lat": ("lat", np.array([51.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([3.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"date_created": "2024-03-15T00:00:00Z"},
    ).to_netcdf(atemporal_netcdf_file)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path, source_uri=str(atemporal_netcdf_file)),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.stac_item["properties"]["datetime"].startswith("2024-03-15T00:00:00")
    assert "start_datetime" not in response.stac_item["properties"]


@integration
def test_service_rejects_atemporal_dataset_without_override(tmp_path, atemporal_netcdf_file):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path, source_uri=str(atemporal_netcdf_file)),
                access_token="token",
                http_client=client,
            )

    with pytest.raises(ValueError, match="start_datetime.*end_datetime"):
        asyncio.run(run())


@integration
def test_service_rejects_spatially_empty_dataset_without_bbox_override(tmp_path):
    path = tmp_path / "nogeo.nc"
    xr.Dataset({"value": (("x",), np.array([1.0]))}).to_netcdf(path)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await netcdf_to_stac_item(
                _request(tmp_path, source_uri=str(path)),
                access_token="token",
                http_client=client,
            )

    with pytest.raises(ValueError, match="bbox"):
        asyncio.run(run())
