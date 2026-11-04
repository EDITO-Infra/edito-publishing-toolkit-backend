"""Tests for STAC primitives: retrying reads and tree traversal.

``read_stac`` / ``is_stac_api_catalog`` are infrastructure primitives
(``ept.infrastructure.formats.stac``); ``collect_stac_tree`` is the
validate_stac feature's traversal orchestration composing them.
"""

from __future__ import annotations

import asyncio

import httpx

from ept.features.validate_stac.validation import collect_stac_tree
from ept.infrastructure.formats.stac import (
    CollectedObject,
    StacObjectType,
    is_stac_api_catalog,
    read_stac,
)
from tests.conftest import unit

ROOT_URL = "https://example.com/data"


# ─── Retrying read (infrastructure primitive) ───────────────────────────────


@unit
def test_read_stac_sends_bearer() -> None:
    """Every GET forwards the caller's bearer token in the Authorization header."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("Authorization", "")))
        return httpx.Response(200, json={"ok": True})

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await read_stac(
                "https://example.com/child.json",
                client=client,
                headers={"Authorization": "Bearer secret-token"},
                timeout=30,
            )

    assert asyncio.run(run()) == {"ok": True}
    assert seen[0][1] == "Bearer secret-token"


def _landing() -> dict:
    return {
        "id": "api",
        "type": "Catalog",
        "stac_version": "1.1.0",
        "description": "STAC API root",
        "conformsTo": [
            "https://api.stacspec.org/v1.0.0/core",
            "https://api.stacspec.org/v1.0.0/collections",
        ],
        "links": [
            {"rel": "self", "href": ROOT_URL, "type": "application/json"},
            {"rel": "data", "href": "/data/collections", "type": "application/json"},
        ],
    }


def _collection(cid: str) -> dict:
    return {
        "id": cid,
        "type": "Collection",
        "stac_version": "1.1.0",
        "description": f"collection {cid}",
        "license": "CC0-1.0",
        "extent": {
            "spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]},
            "temporal": {"interval": [["2024-01-01T00:00:00Z", None]]},
        },
        "links": [
            {
                "rel": "items",
                "href": f"/data/collections/{cid}/items",
                "type": "application/geo+json",
            }
        ],
    }


def _item(iid: str, self_href: str | None = None) -> dict:
    d = {
        "id": iid,
        "type": "Feature",
        "stac_version": "1.1.0",
        "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
        "bbox": [0.5, 0.5, 0.5, 0.5],
        "properties": {"datetime": "2024-06-01T00:00:00Z"},
        "assets": {},
        "links": [],
    }
    if self_href:
        d["links"].append({"rel": "self", "href": self_href})
    return d


def _api_payloads() -> dict[str, dict]:
    return {
        ROOT_URL: _landing(),
        f"{ROOT_URL}/collections": {
            "collections": [_collection("c1"), _collection("c2")],
            "links": [{"rel": "next", "href": "/data/collections?page=2"}],
        },
        f"{ROOT_URL}/collections?page=2": {
            "collections": [_collection("c3")],
            "links": [],
        },
        f"{ROOT_URL}/collections/c1": _collection("c1"),
        f"{ROOT_URL}/collections/c2": _collection("c2"),
        f"{ROOT_URL}/collections/c3": _collection("c3"),
        f"{ROOT_URL}/collections/c1/items": {
            "type": "FeatureCollection",
            "features": [_item("i1", self_href="/data/collections/c1/items/i1")],
            "links": [],
        },
        f"{ROOT_URL}/collections/c2/items": {
            "type": "FeatureCollection",
            # no rel=self → fragment URL fallback
            "features": [_item("i2")],
            "links": [],
        },
        f"{ROOT_URL}/collections/c3/items": {
            "type": "FeatureCollection",
            "features": [],
            "links": [],
        },
    }


def _mock_client(payloads: dict[str, dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?", 1)[0]
        if str(request.url) in payloads:
            return httpx.Response(200, json=payloads[str(request.url)])
        if url in payloads:
            return httpx.Response(200, json=payloads[url])
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _collect_batches(client: httpx.AsyncClient, **kwargs: object) -> list[list[CollectedObject]]:
    async def run() -> list[list[CollectedObject]]:
        batches = []
        async for batch in collect_stac_tree(
            root_url=kwargs.get("root_url", ROOT_URL),
            access_token=None,
            timeout=5,
            http_client=client,
            max_depth=kwargs.get("max_depth", 3),
            collect_children=kwargs.get("collect_children", True),
            max_objects=kwargs.get("max_objects", 100),
            max_concurrency=4,
        ):
            batches.append(batch)
        return batches

    return asyncio.run(run())


# ─── Detection ──────────────────────────────────────────────────────────────


@unit
def test_is_stac_api_catalog_detects_conformance_and_data_link() -> None:
    assert is_stac_api_catalog(_landing()) is True

    static_root = {
        "id": "static",
        "type": "Catalog",
        "stac_version": "1.1.0",
        "description": "static",
        "links": [
            {"rel": "child", "href": "child.json"},
            {"rel": "data", "href": "collections"},
        ],
    }
    # rel="data" alone also marks an API root (static catalogs don't use it)
    assert is_stac_api_catalog(static_root) is True

    plain = {
        "id": "plain",
        "type": "Catalog",
        "stac_version": "1.1.0",
        "description": "plain static catalog",
        "links": [{"rel": "child", "href": "child.json"}],
    }
    assert is_stac_api_catalog(plain) is False

    assert is_stac_api_catalog(_collection("c1")) is False


# ─── API-mode traversal ─────────────────────────────────────────────────────


@unit
def test_api_traversal_walks_collections_items_and_pages() -> None:
    client = _mock_client(_api_payloads())
    batches = _collect_batches(client)
    asyncio.run(client.aclose())

    flat = [obj for batch in batches for obj in batch]

    # root + 3 collections + 2 non-empty item pages; collection pages are not
    # tree nodes (they are not STAC objects — only their collections are)
    urls = {obj.url for obj in flat}
    assert ROOT_URL in urls
    assert f"{ROOT_URL}/collections" not in urls
    assert f"{ROOT_URL}/collections?page=2" not in urls
    for cid in ("c1", "c2", "c3"):
        assert f"{ROOT_URL}/collections/{cid}" in urls
    # pages and empty pages are not tree nodes
    assert f"{ROOT_URL}/collections/c3/items" not in urls

    by_url = {obj.url: obj for obj in flat}

    # root is a catalog at depth 0, first batch, alone
    root = by_url[ROOT_URL]
    assert root.depth == 0
    assert root.obj_type is StacObjectType.CATALOG
    assert batches[0][0].url == ROOT_URL

    # collections at depth 1, parent = root
    c1 = by_url[f"{ROOT_URL}/collections/c1"]
    assert c1.obj_type is StacObjectType.COLLECTION
    assert c1.depth == 1
    assert c1.parent_url == ROOT_URL

    # items at depth 2, parent = their collection, unique URLs, parsed as items
    i1 = by_url[f"{ROOT_URL}/collections/c1/items/i1"]
    assert i1.obj_type is StacObjectType.ITEM
    assert i1.depth == 2
    assert i1.parent_url == f"{ROOT_URL}/collections/c1"
    assert i1.data is not None and i1.data["id"] == "i1"

    i2 = next(u for u in urls if "#items/" in u)
    assert by_url[i2].obj_type is StacObjectType.ITEM


@unit
def test_api_traversal_respects_validate_children_false() -> None:
    client = _mock_client(_api_payloads())
    batches = _collect_batches(client, collect_children=False)
    asyncio.run(client.aclose())
    assert len(batches) == 1
    assert [obj.url for obj in batches[0]] == [ROOT_URL]


@unit
def test_api_traversal_respects_max_objects() -> None:
    client = _mock_client(_api_payloads())
    batches = _collect_batches(client, max_objects=3)
    asyncio.run(client.aclose())
    flat = [obj for batch in batches for obj in batch]
    assert len(flat) <= 3


@unit
def test_static_traversal_follows_items_link_and_paginates() -> None:
    """Collections exposing items only via rel=\"items\" (API-style) still get
    their items validated, including rel=\"next\" pages (EDITO layout)."""
    root_url = "https://example.com/catalog.json"
    coll_url = "https://example.com/collection.json"
    items1 = "https://example.com/items?page=1"
    items2 = "https://example.com/items?page=2"
    payloads = {
        root_url: {
            "id": "root",
            "type": "Catalog",
            "stac_version": "1.1.0",
            "description": "static root",
            "links": [{"rel": "child", "href": "collection.json"}],
        },
        coll_url: {
            **_collection("c1"),
            "links": [
                {"rel": "items", "href": "items?page=1", "type": "application/geo+json"}
            ],
        },
        items1: {
            "type": "FeatureCollection",
            "features": [_item("i1", self_href="/items/i1")],
            "links": [{"rel": "next", "href": "items?page=2"}],
        },
        items2: {
            "type": "FeatureCollection",
            "features": [_item("i2")],
            "links": [],
        },
    }
    client = _mock_client(payloads)
    batches = _collect_batches(client, root_url=root_url)
    asyncio.run(client.aclose())

    flat = [obj for batch in batches for obj in batch]
    by_url = {obj.url: obj for obj in flat}
    i1 = by_url["https://example.com/items/i1"]
    i2 = next(o for u, o in by_url.items() if "#items/" in u)
    assert i1.obj_type is StacObjectType.ITEM
    assert i2.obj_type is StacObjectType.ITEM
    for item in (i1, i2):
        assert item.depth == 2
        assert item.parent_url == coll_url
    assert by_url[coll_url].depth == 1


@unit
def test_api_traversal_follows_static_child_links_too() -> None:
    """Hybrid mode: a collection nested under an API root via static child
    links is still discovered (and its items endpoint walked)."""
    root_url = "https://api.example.com/data"
    nested = "https://api.example.com/data/catalogs/projects/x"
    payloads = {
        root_url: {
            "id": "api",
            "type": "Catalog",
            "stac_version": "1.1.0",
            "description": "api root",
            "conformsTo": ["https://api.stacspec.org/v1.0.0/core"],
            "links": [{"rel": "child", "href": nested}],
        },
        nested: {
            "id": "nested",
            "type": "Catalog",
            "stac_version": "1.1.0",
            "description": "nested static catalog",
            "links": [{"rel": "child", "href": f"{root_url}/collections/c1"}],
        },
        f"{root_url}/collections": {
            "collections": [],
            "links": [],
        },
        f"{root_url}/collections/c1": _collection("c1"),
        f"{root_url}/collections/c1/items": {
            "type": "FeatureCollection",
            "features": [_item("i1", self_href=f"{root_url}/collections/c1/items/i1")],
            "links": [],
        },
    }
    client = _mock_client(payloads)
    batches = _collect_batches(client, root_url=root_url)
    asyncio.run(client.aclose())

    urls = {obj.url for batch in batches for obj in batch}
    assert nested in urls
    assert f"{root_url}/collections/c1" in urls
    assert f"{root_url}/collections/c1/items/i1" in urls


# ─── Static-mode regression (refactored BFS) ────────────────────────────────


@unit
def test_static_traversal_still_follows_child_item_links() -> None:
    parent_url = "https://example.com/catalog.json"
    child_url = "https://example.com/child.json"
    item_url = "https://example.com/item.json"
    payloads = {
        parent_url: {
            "id": "root",
            "type": "Catalog",
            "stac_version": "1.1.0",
            "description": "static root",
            "links": [
                {"rel": "child", "href": "child.json"},
                {"rel": "item", "href": "item.json"},
            ],
        },
        child_url: {
            "id": "child",
            "type": "Catalog",
            "stac_version": "1.1.0",
            "description": "child catalog",
            "links": [{"rel": "item", "href": "item.json"}],
        },
        item_url: _item("item-1"),
    }
    client = _mock_client(payloads)
    batches = _collect_batches(client, root_url=parent_url)
    asyncio.run(client.aclose())

    flat = [obj for batch in batches for obj in batch]
    by_url = {obj.url: obj for obj in flat}
    assert set(by_url) == {parent_url, child_url, item_url}
    assert by_url[parent_url].depth == 0
    assert by_url[child_url].depth == 1
    assert by_url[child_url].parent_url == parent_url
    assert by_url[item_url].obj_type is StacObjectType.ITEM
    assert by_url[item_url].parent_url == parent_url
