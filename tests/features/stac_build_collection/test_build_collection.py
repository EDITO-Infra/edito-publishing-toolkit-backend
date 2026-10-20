"""Unit tests for the STAC Collection builder feature."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from ept.features.stac_build_collection.models import BuildCollectionRequest
from ept.features.stac_build_collection.service import build_collection
from tests.conftest import unit


def _run(request: BuildCollectionRequest) -> dict:
    async def run() -> dict:
        async with httpx.AsyncClient() as client:
            return await build_collection(request, http_client=client)

    return asyncio.run(run())


_EXTENT = {
    "spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]},
    "temporal": {"interval": [["2025-01-01T00:00:00Z", None]]},
}


@unit
def test_collection_builds_extent_license_providers_and_links():
    payload = _run(
        BuildCollectionRequest(
            id="obs-2025",
            description="Observations",
            extent=_EXTENT,
            license="CC-BY-4.0",
            providers=[{"name": "VLIZ", "roles": ["producer"]}],
            links=[
                {"rel": "root", "href": "s3://b/root.json"},
                {"rel": "child", "href": "s3://b/item-1.json"},
            ],
        )
    )
    obj = payload["stac_object"]
    assert obj["type"] == "Collection"
    assert obj["license"] == "CC-BY-4.0"
    assert obj["providers"][0]["name"] == "VLIZ"
    assert obj["extent"]["spatial"]["bbox"] == [[0.0, 0.0, 1.0, 1.0]]
    links = {(link["rel"], link["href"]) for link in obj["links"]}
    assert ("root", "s3://b/root.json") in links
    assert ("child", "s3://b/item-1.json") in links
    assert payload["warnings"] == []


@unit
def test_collection_defaults_license_and_warns_without_extent():
    payload = _run(BuildCollectionRequest(id="obs", description="Obs"))
    assert payload["stac_object"]["license"] == "proprietary"
    assert any("extent" in warning for warning in payload["warnings"])
    assert any("fell back" in warning for warning in payload["warnings"]) is False


@unit
def test_collection_overlay_from_json_with_overrides():
    base = {
        "type": "Collection",
        "id": "obs",
        "description": "Old",
        "license": "proprietary",
        "providers": [{"name": "OLD"}],
    }
    payload = _run(
        BuildCollectionRequest(
            id="obs",
            description="New",
            stac_json=base,
            license="CC-BY-4.0",
            extent=_EXTENT,
        )
    )
    obj = payload["stac_object"]
    assert obj["description"] == "New"
    assert obj["license"] == "CC-BY-4.0"
    assert obj["providers"] == [{"name": "OLD"}]  # base keys survive when not overridden
    assert obj["extent"] == _EXTENT


@unit
def test_collection_source_url_base(monkeypatch):
    async def fake_resolve_base(stac_json, source_url, *, client, access_token=None):
        return {"type": "Collection", "id": "remote", "description": "d", "license": "CC-BY-4.0"}

    monkeypatch.setattr("ept.features.stac_build_collection.service.resolve_base", fake_resolve_base)
    payload = _run(BuildCollectionRequest(id="copy", source_url="https://example.com/collection.json"))
    assert payload["stac_object"]["id"] == "copy"
    assert payload["stac_object"]["license"] == "CC-BY-4.0"


@unit
def test_collection_rejects_unknown_fields_and_unsafe_urls():
    with pytest.raises(ValidationError):
        BuildCollectionRequest(id="x", assets={"a": {"href": "s3://b/k"}})
    with pytest.raises(ValidationError):
        BuildCollectionRequest(id="x", source_url="http://169.254.169.254/latest/meta-data/")
