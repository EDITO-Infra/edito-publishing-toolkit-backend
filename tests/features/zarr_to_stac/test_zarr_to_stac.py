"""Integration test: end-to-end Zarr → STAC Item with a mocked EDITO STAC API."""

import asyncio
import json

import httpx
import numpy as np
import pytest
import xarray as xr

from ept.features.zarr_to_stac.adapters import asset_href
from ept.features.zarr_to_stac.models import ZarrToStacItemRequest
from ept.features.zarr_to_stac.service import zarr_to_stac_item
from tests.conftest import integration, unit


def _handler(request: httpx.Request) -> httpx.Response:
    """Stand-in for the EDITO STAC API /collections endpoint."""
    if request.url.path.endswith("/collections"):
        return httpx.Response(
            200,
            json={
                "collections": [
                    {"id": "bathymetry", "title": "Bathymetry products"},
                    {"id": "temperature", "keywords": ["sea_water_potential_temperature"]},
                ],
                "links": [],
            },
        )
    return httpx.Response(404, json={"detail": "not found"})


def _request(tmp_path, source, **overrides) -> ZarrToStacItemRequest:
    fields = {
        "source_uri": source,
        "local_output": str(tmp_path),
        "endpoint_url": "https://s3.example.test",
        "access_key_id": "key",
        "secret_access_key": "secret",
        **overrides,
    }
    return ZarrToStacItemRequest.model_validate(fields)


@pytest.fixture
def zarr_store(tmp_path):
    path = tmp_path / "observations.zarr"
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
    ).to_zarr(path)
    return str(path)


@pytest.fixture
def atemporal_zarr_store(tmp_path):
    path = tmp_path / "climatology.zarr"
    xr.Dataset(
        data_vars={
            "habitat_suitability": (("lat", "lon"), np.full((2, 2), 0.5)),
        },
        coords={
            "lat": ("lat", np.array([51.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([3.0, 4.0]), {"units": "degrees_east"}),
        },
    ).to_zarr(path)
    return str(path)


@integration
def test_service_suggests_but_never_attaches_collection(tmp_path, zarr_store):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await zarr_to_stac_item(
                _request(tmp_path, zarr_store), access_token="token", http_client=client
            )

    response = asyncio.run(run())

    assert response.collection_id is None
    assert response.collection_source == "standalone"
    assert response.suggested_collection_id == "temperature"
    assert "collection" not in response.stac_item
    assert response.stac_item["properties"]["start_datetime"].startswith("2025-03-01")
    assert response.dataset.bbox == [3.0, 51.0, 4.0, 52.0]

    # local_output is a directory: the file name is <item_id>.json.
    on_disk = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))
    assert on_disk == response.stac_item


@integration
def test_service_uses_explicit_collection(tmp_path, zarr_store):
    def no_api(request: httpx.Request) -> httpx.Response:
        raise AssertionError("collection_id must not trigger an EDITO STAC API call")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(no_api)) as client:
            return await zarr_to_stac_item(
                _request(tmp_path, zarr_store, collection_id="explicit"),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.collection_id == "explicit"
    assert response.collection_source == "provided"
    assert response.suggested_collection_id is None
    assert response.stac_item["collection"] == "explicit"


@integration
def test_service_uses_unpublished_collection_id(tmp_path, zarr_store):
    """A collection_id that does not exist in EDITO STAC yet is used as-is —
    attaching is the caller's decision, not an existence check."""
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await zarr_to_stac_item(
                _request(
                    tmp_path,
                    zarr_store,
                    collection_id="my-own-tutorial-chemistry",
                ),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.collection_id == "my-own-tutorial-chemistry"
    assert response.collection_source == "provided"
    assert response.stac_item["collection"] == "my-own-tutorial-chemistry"


@integration
def test_service_applies_temporal_override_for_atemporal_store(tmp_path, atemporal_zarr_store):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await zarr_to_stac_item(
                _request(
                    tmp_path,
                    atemporal_zarr_store,
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
def test_service_uses_date_attribute_instant(tmp_path):
    source = str(tmp_path / "dated.zarr")
    xr.Dataset(
        {"habitat_suitability": (("lat", "lon"), np.full((2, 2), 0.5))},
        coords={
            "lat": ("lat", np.array([51.0, 52.0]), {"units": "degrees_north"}),
            "lon": ("lon", np.array([3.0, 4.0]), {"units": "degrees_east"}),
        },
        attrs={"date_created": "2024-03-15T00:00:00Z"},
    ).to_zarr(source)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await zarr_to_stac_item(
                _request(tmp_path, source),
                access_token="token",
                http_client=client,
            )

    response = asyncio.run(run())

    assert response.stac_item["properties"]["datetime"].startswith("2024-03-15T00:00:00")
    assert "start_datetime" not in response.stac_item["properties"]


@integration
def test_service_rejects_atemporal_store_without_override(tmp_path, atemporal_zarr_store):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
            return await zarr_to_stac_item(
                _request(tmp_path, atemporal_zarr_store),
                access_token="token",
                http_client=client,
            )

    with pytest.raises(ValueError, match="start_datetime.*end_datetime"):
        asyncio.run(run())


@unit
def test_asset_href_rewrites_s3_to_https_endpoint():
    assert (
        asset_href(
            "s3://oidc-user/ept-tutorial/arco/store.zarr",
            "https://minio.dive.edito.eu",
        )
        == "https://minio.dive.edito.eu/oidc-user/ept-tutorial/arco/store.zarr"
    )


@unit
def test_asset_href_passthrough_non_s3_and_missing_endpoint():
    assert (
        asset_href("https://example.com/store.zarr", None)
        == "https://example.com/store.zarr"
    )
    assert asset_href("s3://bucket/store.zarr", None) == "s3://bucket/store.zarr"
