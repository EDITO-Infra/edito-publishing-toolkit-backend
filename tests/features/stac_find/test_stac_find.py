"""Unit tests for stac_find primitives: filtering, pagination, and param parsing."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ept.features.stac_find import service
from ept.features.stac_find.models import (
    FindCatalogsResponse,
    FindCollectionsResponse,
    FindItemsResponse,
    FindPropertiesResponse,
)
from ept.features.stac_find.routes import _parse_bbox, _parse_csv, _parse_match
from tests.conftest import integration, unit


class FakeStacApi:
    """Minimal EditoStacApiClient stand-in: canned GETs by path."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    async def get_json(self, path: str, *, access_token: str) -> Any:
        self.requested.append(path)
        return self.responses[path]


def _item(item_id: str, *, platform: str = "sentinel-1", asset_key: str | None = None) -> dict:
    assets = {asset_key: {"href": f"https://assets.test/{item_id}.tif", "title": asset_key}} if asset_key else {}
    return {
        "type": "Feature",
        "id": item_id,
        "properties": {"datetime": "2024-06-01T00:00:00Z", "platform": platform, "title": f"Tile {item_id}"},
        "assets": assets,
        "links": [{"rel": "self", "href": f"https://api.dive.edito.eu/data/collections/coll/items/{item_id}"}],
    }


def _run(coro):
    return asyncio.run(coro)


@unit
def test_find_item_gets_search_and_filters_by_q():
    api = FakeStacApi(
        {
            "search?limit=10&bbox=1%2C2%2C3%2C4"
            "&datetime=2024-01-01T00%3A00%3A00Z%2F2024-12-31T23%3A59%3A59Z"
            "&collections=coll-a": {
                "type": "FeatureCollection",
                "features": [_item("keep-me"), _item("drop-me")],
            }
        }
    )
    result = _run(
        service.find_item(
            api,  # type: ignore[arg-type]
            access_token="t",
            bbox=[1, 2, 3, 4],
            datetime="2024-01-01T00:00:00Z/2024-12-31T23:59:59Z",
            collections=["coll-a"],
            q="keep",
        )
    )
    assert isinstance(result, FindItemsResponse)
    assert [f["id"] for f in result.features] == ["keep-me"]
    # q filters client-side — it is never sent upstream
    assert len(api.requested) == 1
    assert api.requested[0].startswith("search?")
    assert "q=" not in api.requested[0]


@unit
def test_find_collection_follows_next_and_filters():
    api = FakeStacApi(
        {
            "collections?limit=100": {
                "collections": [{"id": "matching-title", "title": "Bathymetry"}, {"id": "other"}],
                "links": [{"rel": "next", "href": "https://api.dive.edito.eu/data/collections?offset=2"}],
            },
            "collections?offset=2": {
                "collections": [{"id": "second-page", "description": "nothing"}],
                "links": [],
            },
        }
    )
    result = _run(service.find_collection(api, access_token="t", q="bathy"))  # type: ignore[arg-type]
    assert isinstance(result, FindCollectionsResponse)
    assert [c["id"] for c in result.collections] == ["matching-title"]
    assert api.requested == ["collections?limit=100", "collections?offset=2"]


@unit
def test_find_catalog_walks_child_catalogs_recursively():
    api = FakeStacApi(
        {
            "/catalogs/projects": {
                "type": "Catalog",
                "id": "projects",
                "links": [
                    {"rel": "child", "href": "https://api.dive.edito.eu/data/catalogs/projects/demo", "title": "Demo project"},
                    {"rel": "child", "href": "https://api.dive.edito.eu/data/catalogs/projects/other", "title": "Other"},
                    {"rel": "self", "href": "https://api.dive.edito.eu/data/catalogs/projects"},
                ],
            },
            "catalogs/projects/demo": {
                "type": "Catalog",
                "id": "demo",
                "links": [
                    {"rel": "child", "href": "https://api.dive.edito.eu/data/catalogs/projects/demo/nested", "title": "Nested demo"},
                ],
            },
            "catalogs/projects/demo/nested": {
                "type": "Catalog",
                "id": "nested",
                "links": [],
            },
            "catalogs/projects/other": {
                "type": "Catalog",
                "id": "other",
                "links": [],
            },
        }
    )
    result = _run(service.find_catalog(api, access_token="t", q="demo"))  # type: ignore[arg-type]
    assert isinstance(result, FindCatalogsResponse)
    assert [c["title"] for c in result.catalogs] == ["Demo project", "Nested demo"]
    assert api.requested == [
        "/catalogs/projects",
        "catalogs/projects/demo",
        "catalogs/projects/other",
        "catalogs/projects/demo/nested",
    ]


@unit
def test_find_catalog_deduplicates_child_hrefs_across_parents():
    shared = "https://api.dive.edito.eu/data/catalogs/projects/shared"
    api = FakeStacApi(
        {
            "/catalogs/projects": {
                "links": [
                    {"rel": "child", "href": shared, "title": "Shared"},
                ],
            },
            "catalogs/projects/shared": {"links": []},
        }
    )
    result = _run(service.find_catalog(api, access_token="t"))  # type: ignore[arg-type]
    assert len(result.catalogs) == 1
    assert api.requested == ["/catalogs/projects", "catalogs/projects/shared"]


@unit
def test_find_asset_returns_featurecollection_of_matched_items():
    api = FakeStacApi(
        {
            "search?limit=100": {
                "type": "FeatureCollection",
                "features": [
                    _item("has-asset", asset_key="thumbnail"),
                    _item("no-asset"),
                ],
            }
        }
    )
    result = _run(service.find_asset(api, access_token="t", q="thumb"))  # type: ignore[arg-type]
    assert isinstance(result, FindItemsResponse)
    assert result.type == "FeatureCollection"
    assert [f["id"] for f in result.features] == ["has-asset"]
    assert list(result.features[0]["assets"]) == ["thumbnail"]
    # item links (self) survive the asset filtering
    assert result.features[0]["links"][0]["rel"] == "self"
    assert result.number_returned == 1


@unit
def test_find_asset_scans_all_search_pages():
    late = _item("late-item", asset_key="thumbnail")
    late["assets"]["thumbnail"]["href"] = "https://assets.test/f13fe89f.nc"
    api = FakeStacApi(
        {
            "collections?limit=100": {
                "collections": [{"id": "coll-a", "title": "Coll A"}],
                "links": [],
            },
            "search?limit=100&collections=coll-a": {
                "type": "FeatureCollection",
                "numberMatched": 570,
                "features": [_item("page-one", asset_key="unrelated")],
                "links": [
                    {"rel": "next", "href": "https://api.dive.edito.eu/data/search?collections=coll-a&limit=100&next=42"}
                ],
            },
            "search?collections=coll-a&limit=100&next=42": {
                "type": "FeatureCollection",
                "numberMatched": 570,
                "features": [_item("no-asset"), late],
                "links": [],
            },
        }
    )
    result = _run(service.find_asset(api, access_token="t", q="f13fe89f", collections=["coll-a"]))  # type: ignore[arg-type]
    assert [f["id"] for f in result.features] == ["late-item"]
    # upstream's unfiltered scan size is not reported as number_matched
    assert result.number_matched is None
    assert result.number_returned == 1
    assert api.requested == [
        "collections?limit=100",  # collection resolution
        "search?limit=100&collections=coll-a",
        "search?collections=coll-a&limit=100&next=42",
    ]


@unit
def test_find_properties_matches_all_pairs_case_insensitive():
    api = FakeStacApi(
        {
            "search?limit=100": {
                "type": "FeatureCollection",
                "features": [_item("match", platform="Sentinel-1"), _item("nope", platform="Sentinel-2")],
            }
        }
    )
    result = _run(service.find_properties(api, access_token="t", match={"platform": "sentinel-1"}))  # type: ignore[arg-type]
    assert isinstance(result, FindPropertiesResponse)
    assert [f["id"] for f in result.features] == ["match"]


@unit
def test_route_param_parsers():
    from fastapi import HTTPException

    assert _parse_bbox("1,2,3,4") == [1.0, 2.0, 3.0, 4.0]
    with pytest.raises(HTTPException):
        _parse_bbox("1,2,x")
    assert _parse_csv("a, b,,c") == ["a", "b", "c"]
    assert _parse_csv(None) is None
    assert _parse_match("platform=Sentinel-1,level=L2A") == {"platform": "Sentinel-1", "level": "L2A"}
    with pytest.raises(HTTPException):
        _parse_match("novalue")



@integration
def test_stac_find_routes_require_auth():
    from fastapi.testclient import TestClient

    from ept.api.app import app

    with TestClient(app) as client:
        for path in (
            "/v1/stac/find/items",
            "/v1/stac/find/collections",
            "/v1/stac/find/catalogs",
            "/v1/stac/find/assets?q=x",
            "/v1/stac/find/properties?match=k=v",
        ):
            response = client.get(path)
            assert response.status_code in (401, 403, 422), (path, response.status_code)
