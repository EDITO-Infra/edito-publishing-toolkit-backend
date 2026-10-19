"""Unit tests for the stac_get feature: request boundary and service mapping."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ept.features.stac_build_collection.models import BuildCollectionRequest
from ept.features.stac_get.models import GetStacRequest
from ept.features.stac_get.service import fetch_stac_object
from tests.conftest import unit


@unit
def test_request_rejects_unsafe_fetch_targets():
    """Bad URLs die at the boundary — non-http(s), credentials, private IPs."""
    for url in (
        "file:///etc/passwd",
        "ftp://example.com/catalog.json",
        "https://user:secret@example.com/catalog.json",
        "http://127.0.0.1:8000/catalog.json",
        "http://169.254.169.254/latest/meta-data/",
    ):
        with pytest.raises(ValueError):
            GetStacRequest(url=url)


@unit
def test_request_accepts_public_https_url():
    request = GetStacRequest(url="https://example.com/catalog.json")
    assert str(request.url).startswith("https://example.com")


@unit
def test_fetch_maps_object_type_and_returns_object():
    catalog = {"type": "Catalog", "stac_version": "1.0.0", "id": "c", "description": "d", "links": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization", "") == "Bearer token-1"
        return httpx.Response(200, json=catalog)

    async def run() -> tuple[dict, str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_stac_object(
                "https://example.com/catalog.json",
                http_client=client,
                access_token="token-1",
            )

    stac_object, object_type = asyncio.run(run())
    assert stac_object == catalog
    assert object_type == "catalog"


@unit
def test_fetch_without_token_sends_no_authorization_header():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json={"type": "Feature", "id": "i", "geometry": None, "bbox": [0, 0, 1, 1], "properties": {"datetime": "2025-01-01T00:00:00Z"}, "links": [], "assets": {}})

    async def run() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            _, object_type = await fetch_stac_object(
                "https://example.com/item.json",
                http_client=client,
                access_token=None,
            )
        return object_type

    assert asyncio.run(run()) == "item"


@unit
def test_mixed_rel_links_survive_fetch_and_build_overlay():
    """OBIS-style collections carry many rel types (root/parent/license/
    documentation/item). Every link must survive stac_get verbatim and still be
    present after loading the object into the collection builder (frontend sends
    `links: []` when its form field is empty)."""

    links = [
        {"rel": "root", "href": "../../obis-catalog/catalog.json", "type": "application/json", "title": "Root catalog"},
        {"rel": "parent", "href": "../catalog.json", "type": "application/json", "title": "Root catalog"},
        {"rel": "license", "href": "https://creativecommons.org/licenses/by/4.0/", "type": "text/html", "title": "CC BY 4.0 License"},
        {"rel": "documentation", "href": "https://github.com/iobis/speciesgrids", "type": "text/html", "title": "speciesgrids documentation"},
        {"rel": "item", "href": "./items/speciesgrids-h3-7-000.json", "type": "application/json", "title": "speciesgrids H3-7 quadkey 000"},
        {"rel": "item", "href": "./items/speciesgrids-h3-7-001.json", "type": "application/json", "title": None},
    ]
    collection = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "id": "speciesgrids-h3-7-quadkeys",
        "description": "d",
        "license": "CC-BY-4.0",
        "links": links,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=collection)

    async def run_fetch() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            stac_object, _ = await fetch_stac_object(
                "https://obis-stac.s3.amazonaws.com/collection.json",
                http_client=client,
            )
        return stac_object

    fetched = asyncio.run(run_fetch())
    assert fetched["links"] == links  # stac_get returns every link verbatim

    from ept.features.stac_build_collection.service import build_collection

    payload = asyncio.run(
        build_collection(
            BuildCollectionRequest(id="speciesgrids-h3-7-quadkeys", stac_json=fetched, links=[]),
            http_client=httpx.AsyncClient(),
        )
    )
    built = payload["stac_object"]["links"]
    # build overlay keeps every link; null link fields (pystac "title": null) are dropped
    assert [{key: value for key, value in link.items() if value is not None} for link in links] == built
