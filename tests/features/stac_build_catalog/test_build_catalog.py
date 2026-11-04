"""Unit tests for the STAC Catalog builder feature."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from ept.features.stac_build_catalog.models import BuildCatalogRequest
from ept.features.stac_build_catalog.service import build_catalog
from tests.conftest import unit


def _run(request: BuildCatalogRequest, default_bucket: str | None = None) -> dict:
    async def run() -> dict:
        async with httpx.AsyncClient() as client:
            return await build_catalog(request, http_client=client, default_bucket=default_bucket)

    return asyncio.run(run())


@unit
def test_catalog_builds_with_links_and_extra_fields():
    payload = _run(
        BuildCatalogRequest(
            id="root",
            title="Root",
            description="Root catalog",
            links=[
                {"rel": "root", "href": "s3://b/root.json"},
                {"rel": "child", "href": "s3://b/collection.json"},
            ],
            extra_fields={"conformsTo": ["https://api.stacspec.org/v1.0.0/core"]},
        )
    )
    obj = payload["stac_object"]
    assert obj["type"] == "Catalog"
    assert obj["description"] == "Root catalog"
    links = {(link["rel"], link["href"]) for link in obj["links"]}
    assert ("root", "s3://b/root.json") in links
    assert ("child", "s3://b/collection.json") in links
    assert obj["conformsTo"] == ["https://api.stacspec.org/v1.0.0/core"]
    assert payload["warnings"] == []


@unit
def test_catalog_requires_root_and_child_links_and_description_fallback_warns():
    payload = _run(BuildCatalogRequest(id="root"))
    obj = payload["stac_object"]
    assert obj["description"] == "root"
    assert obj["links"] == []
    assert any("fell back" in warning for warning in payload["warnings"])
    assert any("no root link" in warning for warning in payload["warnings"])
    assert any("no child links" in warning for warning in payload["warnings"])


@unit
def test_catalog_rejects_links_without_rel_or_href():
    with pytest.raises(ValidationError, match="non-empty `rel` and `href`"):
        BuildCatalogRequest(id="x", links=[{"href": "s3://b/c.json"}])
    with pytest.raises(ValidationError, match="non-empty `rel` and `href`"):
        BuildCatalogRequest(id="x", links=[{"rel": "child"}])


@unit
def test_catalog_overlay_from_json():
    base = {
        "type": "Catalog",
        "id": "msfd-ges-stac",
        "description": "ICES catalog",
        "conformsTo": ["https://api.stacspec.org/v1.0.0/core"],
    }
    payload = _run(BuildCatalogRequest(id="msfd-ges-stac", stac_json=base, title="Renamed"))
    obj = payload["stac_object"]
    assert obj["title"] == "Renamed"
    assert obj["description"] == "ICES catalog"  # base survives
    assert obj["conformsTo"] == base["conformsTo"]
    # base has no links and none are provided → empty links + warnings
    assert obj["links"] == []
    assert any("no root link" in warning for warning in payload["warnings"])


@unit
def test_catalog_overlay_keeps_base_links_when_request_links_empty():
    # The frontend sends `links: []` (empty form field) when the user adds none;
    # the base object's links must survive that, and only a non-empty request
    # list replaces them.
    base = {
        "type": "Collection",
        "id": "msfd-good-environmental-status-assessments",
        "description": "MSFD GES",
        "links": [
            {"rel": "root", "href": "../catalog.json", "type": "application/json", "title": "MSFD GES Catalog"},
            {"rel": "item", "href": "./msfd-ges-assessments-article-8-overall-status/msfd-ges-assessments-article-8-overall-status.json", "type": "application/geo+json", "title": None},
            {"rel": "parent", "href": "../catalog.json", "type": "application/json", "title": "MSFD GES Catalog"},
        ],
    }
    payload = _run(BuildCatalogRequest(id="x", stac_json=base, links=[]))
    # null link fields (pystac "title": null) are dropped on merge: schemas want strings
    assert payload["stac_object"]["links"] == [
        {**link, "title": link["title"]} if link.get("title") is not None else {key: value for key, value in link.items() if key != "title"}
        for link in base["links"]
    ]


@unit
def test_catalog_source_url_base_keeps_links_unless_overridden(monkeypatch):
    async def fake_resolve_base(stac_json, source_url, *, client, access_token=None):
        return {"type": "Catalog", "id": "remote", "description": "d", "links": [{"rel": "child", "href": "./c.json"}]}

    monkeypatch.setattr("ept.features.stac_build_catalog.service.resolve_base", fake_resolve_base)
    payload = _run(BuildCatalogRequest(id="copy", source_url="https://example.com/catalog.json"))
    assert payload["stac_object"]["id"] == "copy"
    # base links survive unless the request provides its own links
    assert any(link["href"] == "./c.json" for link in payload["stac_object"]["links"])
    assert any("no root link" in warning for warning in payload["warnings"])

    overridden = _run(
        BuildCatalogRequest(
            id="copy",
            source_url="https://example.com/catalog.json",
            links=[{"rel": "root", "href": "s3://b/root.json"}],
        )
    )
    assert [(link["rel"], link["href"]) for link in overridden["stac_object"]["links"]] == [("root", "s3://b/root.json")]


@unit
def test_catalog_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        BuildCatalogRequest(id="x", extent={"spatial": {}})


@unit
def test_catalog_target_validation():
    # target is an S3 prefix; the object filename is appended automatically,
    # unless the value ends in .json — then it names the file itself.
    with pytest.raises(ValidationError, match="bucket"):
        BuildCatalogRequest(id="x", target="s3://b/x/")
    with pytest.raises(ValidationError, match="S3 prefix"):
        BuildCatalogRequest(id="x", target="https://example.com/x/")
    assert BuildCatalogRequest(id="x", target="x/").target == "x"
    assert BuildCatalogRequest(id="x", target="x/custom.json").target == "x/custom.json"


@unit
def test_catalog_s3_credentials_are_flat_and_not_exported_into_the_object(monkeypatch):
    # Same flat shape as upload_s3 (no nested `s3` object).
    class _FakeFs:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            self.written = (path, payload)

    seen: dict = {}

    def fake_s3_url_to_fs(uri, *, storage_options):
        seen["uri"] = uri
        seen["storage_options"] = storage_options
        return _FakeFs(), "b/x/catalog.json"

    monkeypatch.setattr("ept.features.stac_build_catalog.service.s3_url_to_fs", fake_s3_url_to_fs)
    payload = _run(
        BuildCatalogRequest(
            id="x",
            target="x",
            bucket="b",
            endpoint_url="https://minio.example.test",
            access_key_id="k",
            secret_access_key="s",
            session_token="t",
        )
    )
    obj = payload["stac_object"]
    assert not {"endpoint_url", "access_key_id", "secret_access_key", "session_token"} & obj.keys()
    assert payload["target_uri"] == "s3://b/x/catalog.json"
    assert seen["uri"] == "s3://b/x/catalog.json"
    assert seen["storage_options"]["key"] == "k"
    assert seen["storage_options"]["token"] == "t"
    assert seen["storage_options"]["endpoint_url"] == "https://minio.example.test"


@unit
def test_catalog_default_bucket_from_bearer_token(monkeypatch):
    class _FakeFs:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            self.written = (path, payload)

    seen: dict = {}

    def fake_s3_url_to_fs(uri, *, storage_options):
        seen["uri"] = uri
        return _FakeFs(), "oidc-alice/x/catalog.json"

    monkeypatch.setattr("ept.features.stac_build_catalog.service.s3_url_to_fs", fake_s3_url_to_fs)
    payload = _run(
        BuildCatalogRequest(
            id="x",
            target="x",
            endpoint_url="https://minio.example.test",
            access_key_id="k",
            secret_access_key="s",
        ),
        default_bucket="oidc-alice",
    )
    assert payload["target_uri"] == "s3://oidc-alice/x/catalog.json"
    assert seen["uri"] == "s3://oidc-alice/x/catalog.json"
