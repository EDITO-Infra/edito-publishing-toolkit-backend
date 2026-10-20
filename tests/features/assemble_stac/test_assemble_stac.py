"""Unit tests for the STAC assemble feature."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ept.features.assemble_stac.assemble import StacObject, assemble_tree, export_tree
from ept.features.assemble_stac.models import AssembleStacRequest
from ept.features.assemble_stac.service import assemble_stac
from tests.conftest import unit


def _item(id: str, **extra) -> dict:
    item = {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": id,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
        },
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "properties": {"datetime": "2024-01-01T00:00:00Z"},
        "links": [],
        "assets": {},
    }
    item.update(extra)
    return item


def _collection(id: str, **extra) -> dict:
    data = {
        "type": "Collection",
        "stac_version": "1.1.0",
        "id": id,
        "description": f"{id} description",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [],
    }
    data.update(extra)
    return data


def _catalog(id: str, **extra) -> dict:
    data = {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": id,
        "description": f"{id} description",
        "links": [],
    }
    data.update(extra)
    return data


# ── parent resolution: links first ───────────────────────────────────────────


@unit
def test_item_parent_link_stitches_flat_layout():
    """An item next to the collection in one flat folder is stitched by its parent link."""
    collection = _collection("col-1")
    item = _item("it-1", links=[{"rel": "parent", "href": "collection.json"}])
    root, warnings = assemble_tree(
        [
            StacObject("collection.json", collection),
            StacObject("item-1.json", item),
        ]
    )
    items = list(root.get_items())
    assert [i.id for i in items] == ["it-1"]
    # the flat-layout fallback would have attached the item to the root; the
    # parent link must win, and no folder warning may appear
    assert not any("folder" in w for w in warnings)


@unit
def test_collection_property_beats_parent_link():
    """An item's 'collection' property wins over its 'parent' link."""
    col_a = _collection("col-a")
    col_b = _collection("col-b")
    item = _item(
        "it-1",
        collection="col-b",
        links=[{"rel": "parent", "href": "col-a.json"}],
    )
    root, _ = assemble_tree(
        [
            StacObject("col-a.json", col_a),
            StacObject("col-b.json", col_b),
            StacObject("it-1.json", item),
        ]
    )
    assert [c.id for c in root.get_children()] == ["col-b"]
    assert [i.id for i in list(root.get_children())[0].get_items()] == ["it-1"]


@unit
def test_parent_child_links_build_catalog_of_catalogs():
    """Three levels: catalog -> catalog -> collection -> item, stitched by links."""
    root_cat = _catalog(
        "root",
        links=[{"rel": "child", "href": "sub/catalog.json"}],
    )
    sub_cat = _catalog(
        "sub",
        links=[
            {"rel": "parent", "href": "../catalog.json"},
            {"rel": "child", "href": "col.json"},
        ],
    )
    collection = _collection("col-1")
    item = _item("it-1", links=[{"rel": "parent", "href": "col.json"}])
    root, _ = assemble_tree(
        [
            StacObject("catalog.json", root_cat),
            StacObject("sub/catalog.json", sub_cat),
            StacObject("sub/col.json", collection),
            StacObject("sub/it-1.json", item),
        ]
    )
    subs = list(root.get_children())
    assert [c.id for c in subs] == ["sub"]
    cols = list(subs[0].get_children())
    assert [c.id for c in cols] == ["col-1"]
    assert [i.id for i in cols[0].get_items()] == ["it-1"]


@unit
def test_dangling_parent_link_falls_back_to_folder():
    """A parent link to a file outside the source falls back to folder layout."""
    collection = _collection("col-1")
    item = _item("it-1", links=[{"rel": "parent", "href": "../../elsewhere/collection.json"}])
    root, warnings = assemble_tree(
        [
            StacObject("collection.json", collection),
            StacObject("items/it-1.json", item),
        ]
    )
    # the collection is the root here; the folder fallback attaches the item to it
    assert [i.id for i in root.get_items()] == ["it-1"]


@unit
def test_child_link_to_item_warns_but_attaches():
    """A 'child' link pointing at an item is a spec violation: warn, still attach."""
    collection = _collection("col-1", links=[{"rel": "child", "href": "it-1.json"}])
    item = _item("it-1")
    root, warnings = assemble_tree(
        [
            StacObject("col-1.json", collection),
            StacObject("it-1.json", item),
        ]
    )
    assert [i.id for i in root.get_items()] == ["it-1"]
    assert any("rel='item'" in w for w in warnings)


@unit
def test_root_parent_link_is_ignored_with_warning():
    catalog = _catalog("root", links=[{"rel": "parent", "href": "col-1.json"}])
    collection = _collection("col-1")
    root, warnings = assemble_tree(
        [
            StacObject("catalog.json", catalog),
            StacObject("col-1.json", collection),
        ]
    )
    assert root.id == "root"
    assert any("'parent' link; ignored" in w for w in warnings)


@unit
def test_unknown_collection_property_warns_and_uses_parent_link():
    col_a = _collection("col-a")
    item = _item(
        "it-1",
        collection="missing-col",
        links=[{"rel": "parent", "href": "col-a.json"}],
    )
    root, warnings = assemble_tree(
        [
            StacObject("col-a.json", col_a),
            StacObject("it-1.json", item),
        ]
    )
    assert [i.id for i in root.get_items()] == ["it-1"]
    assert any("matches no known collection id" in w for w in warnings)


# ── fix-ups ──────────────────────────────────────────────────────────────────


@unit
def test_missing_bbox_derived_from_geometry_and_extent_from_items():
    collection = _collection("col-1")
    del collection["extent"]
    item = _item("it-1")
    del item["bbox"]
    del item["links"]
    root, warnings = assemble_tree(
        [
            StacObject("col-1.json", collection),
            StacObject("it-1.json", item),
        ]
    )
    assert root.extent.spatial.bboxes == [[0.0, 0.0, 1.0, 1.0]]
    intervals = [
        [v.isoformat() if v is not None else None for v in interval]
        for interval in root.extent.temporal.intervals
    ]
    assert intervals == [["2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"]]
    assert any("derived 'bbox' from geometry" in w for w in warnings)


@unit
def test_missing_ids_and_stac_version_filled():
    collection = _collection("col-1")
    item = _item("it-1", links=[{"rel": "parent", "href": "collection.json"}])
    del item["stac_version"]
    root, warnings = assemble_tree(
        [
            StacObject("col-1.json", collection),
            StacObject("flat/it-1.json", item),
        ]
    )
    assert list(root.get_items())[0].to_dict()["stac_version"] == "1.1.0"
    assert any("filled missing 'stac_version'" in w for w in warnings)


@unit
def test_no_catalog_or_collection_raises():
    with pytest.raises(ValueError, match="No STAC Catalog or Collection"):
        assemble_tree([StacObject("it-1.json", _item("it-1"))])


# ── export ───────────────────────────────────────────────────────────────────


@unit
def test_export_tree_writes_self_contained_layout(tmp_path: Path):
    collection = _collection("col-1", links=[{"rel": "parent", "href": "catalog.json"}])
    item = _item(
        "it-1",
        links=[
            {"rel": "parent", "href": "col-1.json"},
            {"rel": "collection", "href": "col-1.json"},
        ],
    )
    root, _ = assemble_tree(
        [
            StacObject("catalog.json", _catalog("root")),
            StacObject("col-1.json", collection),
            StacObject("it-1.json", item),
        ]
    )
    target = tmp_path / "out"
    report = export_tree(root, str(target))

    assert report.root_path == "catalog.json"
    assert {e.path for e in report.exported} == {
        "catalog.json",
        "col-1/collection.json",
        "col-1/it-1/it-1.json",
    }
    written = json.loads((target / "col-1" / "it-1" / "it-1.json").read_text())
    hrefs = [link["href"] for link in written["links"]]
    assert all(not h.startswith(("http://", "https://", "/")) for h in hrefs)
    assert written["collection"] == "col-1"


@unit
def test_export_tree_slugifies_unsafe_ids(tmp_path: Path):
    collection = _collection("Weird Col!", links=[{"rel": "parent", "href": "catalog.json"}])
    item = _item(
        "my item",
        links=[
            {"rel": "parent", "href": "col.json"},
            {"rel": "collection", "href": "col.json"},
        ],
    )
    root, _ = assemble_tree(
        [
            StacObject("catalog.json", _catalog("root")),
            StacObject("col.json", collection),
            StacObject("it.json", item),
        ]
    )
    target = tmp_path / "out"
    warnings: list[str] = []
    report = export_tree(root, str(target), warnings=warnings)

    assert {e.path for e in report.exported} == {
        "catalog.json",
        "weird-col/collection.json",
        "weird-col/my-item/my-item.json",
    }
    data = json.loads((target / "weird-col" / "my-item" / "my-item.json").read_text())
    assert data["id"] == "my item"  # id unchanged, only the export name slugged
    assert any("not safe as a file/folder name" in w for w in warnings)


@unit
def test_export_tree_refuses_overwrite_false_on_existing(tmp_path: Path):
    root, _ = assemble_tree([StacObject("catalog.json", _catalog("root"))])
    target = tmp_path / "out"
    target.mkdir()
    (target / "catalog.json").write_text("{}")
    with pytest.raises(FileExistsError):
        export_tree(root, str(target), overwrite=False)


# ── request model ────────────────────────────────────────────────────────────


@unit
def test_request_rejects_local_output_with_s3_credentials():
    with pytest.raises(ValidationError, match="only valid with an s3://"):
        AssembleStacRequest(
            source_uri="./workspace/parts",
            local_output="./workspace/out",
            endpoint_url="https://minio.example.org",
            access_key_id="k",
            secret_access_key="s",
        )


@unit
def test_request_rejects_local_output_with_s3_target():
    with pytest.raises(ValidationError, match="not both"):
        AssembleStacRequest(source_uri="./workspace/parts", target="bucket/out", local_output="./out")
    with pytest.raises(ValidationError, match="target is required"):
        AssembleStacRequest(source_uri="./workspace/parts")


@unit
def test_request_accepts_local_without_credentials():
    request = AssembleStacRequest(source_uri="./workspace/parts", local_output="./workspace/out")
    assert request.overwrite is True
    # S3 output is the default and needs no explicit credentials (env fallback).
    request = AssembleStacRequest(source_uri="./workspace/parts", target="bucket/out")
    assert request.target == "bucket/out"


# ── end-to-end through the service (local dir source and target) ─────────────


@unit
def test_service_assembles_local_parts_directory(tmp_path: Path):
    source = tmp_path / "parts"
    (source / "col-1").mkdir(parents=True)
    (source / "catalog.json").write_text(json.dumps(_catalog("root")))
    (source / "col-1" / "collection.json").write_text(json.dumps(_collection("col-1")))
    (source / "col-1" / "it-1.json").write_text(
        json.dumps(_item("it-1", links=[{"rel": "parent", "href": "collection.json"}]))
    )

    request = AssembleStacRequest(
        source_uri=str(source), local_output=str(tmp_path / "out"), overwrite=True
    )
    response = assemble_stac(request)

    assert response.root_path == "catalog.json"
    assert response.uploaded == []
    assert not response.warnings
    assert {o.path for o in response.objects} == {
        "catalog.json",
        "col-1/collection.json",
        "col-1/it-1/it-1.json",
    }
    # every exported object validated cleanly
    assert all(not o.validation_issues for o in response.objects)
