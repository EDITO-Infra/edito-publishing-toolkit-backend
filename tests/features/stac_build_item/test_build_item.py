"""Unit tests for the STAC Item builder feature."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ept.features.stac_build_item.models import BuildItemRequest
from ept.features.stac_build_item.service import build_item
from tests.conftest import unit


def _request(**overrides) -> BuildItemRequest:
    return BuildItemRequest(**{"id": "item-1", **overrides})


async def _noop_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as client:
        return client


def _run(request: BuildItemRequest, default_bucket: str | None = None) -> dict:
    import asyncio

    async def run() -> dict:
        async with httpx.AsyncClient() as client:
            return await build_item(request, http_client=client, default_bucket=default_bucket)

    return asyncio.run(run())


# ── build from fields ────────────────────────────────────────────────────────


@unit
def test_item_builds_with_datetime_bbox_and_derived_geometry():
    payload = _run(
        _request(
            bbox=[2.5, 51.0, 4.5, 51.6],
            datetime="2025-01-15T10:30:00Z",
            links=[{"rel": "root", "href": "s3://b/root.json"}],
        )
    )
    obj = payload["stac_object"]

    assert obj["type"] == "Feature"
    assert obj["id"] == "item-1"
    assert obj["bbox"] == [2.5, 51.0, 4.5, 51.6]
    assert obj["geometry"]["type"] == "Polygon"
    assert obj["properties"]["datetime"] == "2025-01-15T10:30:00Z"
    assert payload["warnings"] == []
    assert payload["status"] == "preview"


@unit
def test_item_accepts_start_end_interval():
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            start_datetime="2025-01-01T00:00:00Z",
            end_datetime="2025-01-02T00:00:00Z",
        )
    )
    obj = payload["stac_object"]
    assert obj["properties"]["start_datetime"] == "2025-01-01T00:00:00Z"
    assert obj["properties"]["end_datetime"] == "2025-01-02T00:00:00Z"
    assert obj["properties"]["datetime"] is None


@unit
def test_item_title_and_description_go_into_properties():
    payload = _run(_request(datetime="2025-01-01T00:00:00Z", title="T", description="D"))
    obj = payload["stac_object"]
    assert obj["properties"]["title"] == "T"
    assert obj["properties"]["description"] == "D"
    assert "title" not in obj


@unit
def test_item_without_temporal_information_raises_value_error():
    with pytest.raises(ValueError, match="start_datetime"):
        _run(_request(bbox=[0, 0, 1, 1]))


@unit
def test_item_links_pass_through_verbatim():
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            datetime="2025-01-01T00:00:00Z",
            links=[
                {"rel": "root", "href": "s3://bucket/root.json"},
                {"rel": "parent", "href": "s3://bucket/collection.json"},
                {"rel": "derived_from", "href": "https://example.com/source"},
            ],
        )
    )
    links = {(link["rel"], link["href"]) for link in payload["stac_object"]["links"]}
    assert ("parent", "s3://bucket/collection.json") in links
    assert ("root", "s3://bucket/root.json") in links
    assert ("derived_from", "https://example.com/source") in links


@unit
def test_item_assets_collection_and_extra_fields():
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            datetime="2025-01-01T00:00:00Z",
            collection="obs-2025",
            assets={"data": {"href": "s3://bucket/data.zarr"}},
            extra_fields={"eo:cloud_cover": 12},
        )
    )
    obj = payload["stac_object"]
    assert obj["collection"] == "obs-2025"
    assert obj["assets"]["data"]["href"] == "s3://bucket/data.zarr"
    assert obj["eo:cloud_cover"] == 12


# ── overlay modes (json / source_url) ────────────────────────────────────────


@unit
def test_json_base_with_field_overrides():
    base = {
        "type": "Feature",
        "id": "orig",
        "properties": {"datetime": "2024-01-01T00:00:00Z", "platform": "a"},
        "assets": {"data": {"href": "s3://b/old.zarr"}},
    }
    payload = _run(
        _request(
            id="renamed",
            stac_json=base,
            datetime="2025-01-01T00:00:00Z",
            assets={"data": {"href": "s3://b/new.zarr"}},
        )
    )
    obj = payload["stac_object"]
    assert obj["id"] == "renamed"
    assert obj["properties"]["platform"] == "a"  # base keys survive
    assert obj["properties"]["datetime"] == "2025-01-01T00:00:00Z"  # override wins
    assert obj["assets"]["data"]["href"] == "s3://b/new.zarr"


@unit
def test_source_url_base_is_fetched_and_overridden(monkeypatch):
    async def fake_resolve_base(stac_json, source_url, *, client, access_token=None):
        return {"type": "Feature", "id": "remote", "properties": {"datetime": "2024-01-01T00:00:00Z"}, "bbox": [0, 0, 1, 1], "geometry": None}

    monkeypatch.setattr("ept.features.stac_build_item.service.resolve_base", fake_resolve_base)
    payload = _run(_request(id="local-copy", source_url="https://example.com/item.json"))
    obj = payload["stac_object"]
    assert obj["id"] == "local-copy"
    assert obj["bbox"] == [0, 0, 1, 1]


@unit
def test_source_url_base_of_wrong_type_forces_feature(monkeypatch):
    async def fake_resolve_base(stac_json, source_url, *, client, access_token=None):
        return {"type": "Catalog", "id": "c", "description": "d", "properties": {"datetime": "2024-01-01T00:00:00Z"}}

    monkeypatch.setattr("ept.features.stac_build_item.service.resolve_base", fake_resolve_base)
    payload = _run(_request(source_url="https://example.com/catalog.json"))
    assert payload["stac_object"]["type"] == "Feature"
    assert any("no root link" in warning for warning in payload["warnings"])


@unit
def test_base_without_temporal_information_rejected():
    # Schema enforcement: an item without datetime or start/end cannot be built.
    with pytest.raises(ValueError, match="schema validation"):
        _run(_request(id="x", stac_json={"type": "Feature", "id": "x", "geometry": None, "bbox": [0, 0, 1, 1], "properties": {}}))


# ── request validation ───────────────────────────────────────────────────────


@unit
def test_request_rejects_unsafe_source_urls():
    for url in ("http://127.0.0.1:8000/a.json", "ftp://example.com/a.json", "https://u:p@example.com/a.json"):
        with pytest.raises(ValidationError):
            BuildItemRequest(id="x", source_url=url)


@unit
def test_request_rejects_unknown_fields_and_bad_ids():
    with pytest.raises(ValidationError):
        BuildItemRequest(id="x", children=["c.json"])  # not a request field: items use standard links
    with pytest.raises(ValidationError):
        BuildItemRequest(id="with space")


# ── export ───────────────────────────────────────────────────────────────────


@unit
def test_local_export_writes_self_link(tmp_path):
    # local_output is the destination directory; item.json is appended automatically.
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            datetime="2025-01-01T00:00:00Z",
            local_output=str(tmp_path),
        )
    )
    written_path = tmp_path / "item.json"
    written = json.loads(written_path.read_text())
    assert ("self", str(written_path)) in {(link["rel"], link["href"]) for link in written["links"]}
    assert payload["target_uri"] == str(written_path)
    assert payload["exported"] is True
    assert payload["status"] == "completed"


@unit
def test_export_refuses_existing_target_without_overwrite(tmp_path):
    target = tmp_path / "item.json"
    target.write_text("{}", encoding="utf-8")
    request = _request(
        bbox=[0, 0, 1, 1],
        datetime="2025-01-01T00:00:00Z",
        local_output=str(tmp_path),
    )
    with pytest.raises(FileExistsError):
        _run(request)
    assert _run(request.model_copy(update={"overwrite": True}))["exported"] is True


@unit
def test_relative_local_output_resolves_against_ept_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _run(
        _request(bbox=[0, 0, 1, 1], datetime="2025-01-01T00:00:00Z", local_output="workspace/output/catalog-1")
    )
    # Relative local output stays relative in the response and resolves against
    # the EPT working directory at write time.
    expected = Path("workspace") / "output" / "catalog-1" / "item.json"
    assert payload["target_uri"] == str(expected)
    assert expected.is_absolute() is False
    assert (tmp_path / expected).exists()


@unit
def test_default_export_output_is_s3_with_personal_bucket(monkeypatch):
    class _FakeFs:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            self.written = (path, payload)

    seen: dict = {}

    def fake_s3_url_to_fs(uri, *, storage_options):
        seen["uri"] = uri
        return _FakeFs(), "oidc-alice/catalog-1/item.json"

    monkeypatch.setattr("ept.features.stac_build_item.service.s3_url_to_fs", fake_s3_url_to_fs)
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            datetime="2025-01-01T00:00:00Z",
            target="catalog-1",
            endpoint_url="https://minio.example.test",
            access_key_id="k",
            secret_access_key="s",
        ),
        default_bucket="oidc-alice",
    )
    assert payload["target_uri"] == "s3://oidc-alice/catalog-1/item.json"
    assert seen["uri"] == "s3://oidc-alice/catalog-1/item.json"


@unit
def test_explicit_bucket_overrides_default_bucket(monkeypatch):
    class _FakeFs:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            self.written = (path, payload)

    def fake_s3_url_to_fs(uri, *, storage_options):
        return _FakeFs(), "oidc-project/catalog-1/item.json"

    monkeypatch.setattr("ept.features.stac_build_item.service.s3_url_to_fs", fake_s3_url_to_fs)
    payload = _run(
        _request(
            bbox=[0, 0, 1, 1],
            datetime="2025-01-01T00:00:00Z",
            target="catalog-1",
            bucket="oidc-project",
            endpoint_url="https://minio.example.test",
            access_key_id="k",
            secret_access_key="s",
        ),
        default_bucket="oidc-alice",
    )
    assert payload["target_uri"] == "s3://oidc-project/catalog-1/item.json"


@unit
def test_s3_export_without_any_bucket_raises_value_error():
    with pytest.raises(ValueError, match="bucket is required"):
        _run(_request(bbox=[0, 0, 1, 1], datetime="2025-01-01T00:00:00Z", target="catalog-1"))
