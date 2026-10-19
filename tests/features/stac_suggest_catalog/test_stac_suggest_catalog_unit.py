"""Unit tests for the stac_suggest_catalog feature: derivation, assembly, boundaries."""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

import pytest
from pydantic import ValidationError

from ept.features.stac_suggest_catalog.models import SuggestCatalogRequest
from ept.features.stac_suggest_catalog.service import suggest_catalog
from tests.conftest import unit


def _collection(collection_id: str) -> dict:
    return {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": collection_id,
        "description": collection_id,
        "extent": {"spatial": {"bbox": [[0.0, 0.0, 1.0, 1.0]]}, "temporal": {"interval": [[None, None]]}},
        "license": "proprietary",
        "keywords": [f"kw-{collection_id}"],
        "links": [],
    }


def _item(item_id: str, collection_id: str) -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": item_id,
        "collection": collection_id,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "geometry": {"type": "Polygon", "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]},
        "properties": {"datetime": "2024-01-01T00:00:00Z"},
        "links": [],
        "assets": {},
    }


def _request(**overrides) -> SuggestCatalogRequest:
    values = {
        "collections": [_collection("one"), _collection("two")],
        "title": "North Sea Catalog",
    }
    values.update(overrides)
    return SuggestCatalogRequest(**values)


def _tree(root: str, *collections: tuple[str, list[str]]) -> None:
    """Write collection.json + item files under root, the suggest/collection layout."""

    for collection_id, item_ids in collections:
        folder = posixpath.join(root, collection_id)
        Path(folder).mkdir(parents=True, exist_ok=True)
        collection = _collection(collection_id)
        collection["links"] = [{"rel": "item", "href": f"{item}/{item}.json"} for item in item_ids]
        with open(posixpath.join(folder, "collection.json"), "w") as handle:
            json.dump(collection, handle)
        for item_id in item_ids:
            item_path = posixpath.join(folder, item_id, f"{item_id}.json")
            Path(item_path).parent.mkdir(parents=True, exist_ok=True)
            with open(item_path, "w") as handle:
                json.dump(_item(item_id, collection_id), handle)


@unit
def test_request_rejects_bad_inputs():
    with pytest.raises(ValidationError, match="collections, collection_links, source_folder"):
        SuggestCatalogRequest()
    with pytest.raises(ValidationError, match="type == 'Collection'"):
        _request(collections=[{"type": "Feature", "id": "x"}])
    with pytest.raises(ValidationError, match="Catalog or Collection"):
        _request(collections=[], base={"type": "Feature", "id": "x"})
    with pytest.raises(ValidationError, match="flat"):
        _request(id="a/../b")
    with pytest.raises(ValidationError, match="flat"):
        _request(id="a/b")


@unit
def test_id_rules_explicit_base_username_error():
    explicit = suggest_catalog(_request(id="oidc-user-my-catalog"), default_username="oidc-user")
    assert explicit["stac_object"]["id"] == "oidc-user-my-catalog"

    # Base = an existing EDITO catalog id from mycatalogs; its last segment + slug.
    from_base = suggest_catalog(_request(base={"type": "Catalog", "id": "projects/oidc-user"}))
    assert from_base["stac_object"]["id"] == "oidc-user-north-sea-catalog"

    from_username = suggest_catalog(_request(), default_username="oidc-user")
    assert from_username["stac_object"]["id"] == "oidc-user-north-sea-catalog"

    with pytest.raises(ValueError, match="Cannot propose a catalog id"):
        suggest_catalog(_request(), default_username=None)


@unit
def test_base_metadata_carried_and_type_normalized_to_catalog():
    base = {
        "type": "Collection",
        "id": "projects/oidc-user/source",
        "title": "Source",
        "description": "Copied description",
    }
    payload = suggest_catalog(_request(base=base, title=None))
    obj = payload["stac_object"]
    assert obj["type"] == "Catalog"
    assert obj["id"] == "source-source"
    assert obj["title"] == "Source"
    assert obj["description"] == "Copied description"


@unit
def test_child_collection_ids_reported_and_links_empty():
    payload = suggest_catalog(_request(), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["links"] == []
    assert any("one" in warning for warning in payload["warnings"])


@unit
def test_description_fallback_warns():
    payload = suggest_catalog(_request(), default_username="oidc-user")
    assert payload["stac_object"]["description"] == "North Sea Catalog"
    assert any("description fell back" in warning for warning in payload["warnings"])

    explicit = suggest_catalog(_request(description="Explicit"), default_username="oidc-user")
    assert explicit["stac_object"]["description"] == "Explicit"
    assert not any("description fell back" in warning for warning in explicit["warnings"])


@unit
def test_schema_valid_output():
    payload = suggest_catalog(_request(), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["type"] == "Catalog"
    assert obj["stac_version"] == "1.1.0"
    assert payload["status"] == "suggested"


@unit
def test_metadata_enriched_from_collections():
    payload = suggest_catalog(_request(), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["keywords"] == ["kw-one", "kw-two"]
    assert any("keywords unioned" in warning for warning in payload["warnings"])

    own = suggest_catalog(
        _request(base={"type": "Catalog", "id": "b", "keywords": ["mine"]}),
        default_username="oidc-user",
    )
    assert own["stac_object"]["keywords"] == ["mine", "kw-one", "kw-two"]


def _assemble(tmp_path, **overrides) -> tuple[dict, object]:
    source = tmp_path / "collections"
    _tree(str(source), ("one", ["item-one"]), ("two", ["item-two"]))
    request = _request(
        collections=[],
        source_folder=str(source),
        local_output=str(tmp_path / "stac-final"),
        **overrides,
    )
    return suggest_catalog(request, default_username="oidc-user"), tmp_path / "stac-final"


@unit
def test_assemble_exports_linked_tree(tmp_path):
    payload, final = _assemble(tmp_path)

    assert payload["status"] == "completed"
    assert payload["exported"] is True
    catalog = json.loads((final / "catalog.json").read_text())
    assert catalog["id"] == "oidc-user-north-sea-catalog"
    assert {link["href"] for link in catalog["links"] if link["rel"] == "child"} == {
        "collections/one/collection.json",
        "collections/two/collection.json",
    }
    assert {link["href"] for link in catalog["links"] if link["rel"] == "root"} == {"catalog.json"}

    collection = json.loads((final / "collections" / "one" / "collection.json").read_text())
    assert {link["href"] for link in collection["links"] if link["rel"] in {"root", "parent"}} == {"../../catalog.json"}
    assert {link["href"] for link in collection["links"] if link["rel"] == "item"} == {"item-one/item-one.json"}

    item = json.loads((final / "collections" / "one" / "item-one" / "item-one.json").read_text())
    assert {link["href"] for link in item["links"] if link["rel"] == "root"} == {"../../../catalog.json"}
    assert {link["href"] for link in item["links"] if link["rel"] in {"parent", "collection"}} == {"../collection.json"}
    assert item["collection"] == "one"

    uris = payload["collection_uris"] + payload["item_uris"]
    assert len(uris) == 4
    assert all(uri.startswith(str(final)) for uri in uris)


@unit
def test_collection_links_assemble_and_preview(tmp_path):
    source = tmp_path / "collections"
    _tree(str(source), ("one", ["item-one"]), ("two", ["item-two"]))
    links = [
        str(source / "one" / "collection.json"),
        str(source / "two" / "collection.json"),
    ]

    # No target → validated preview, nothing written.
    preview = suggest_catalog(_request(collections=[], collection_links=links), default_username="oidc-user")
    assert preview["status"] == "suggested"
    assert preview["exported"] is False
    assert not (tmp_path / "stac-final").exists()

    # With a target → full tree assembly.
    payload = suggest_catalog(
        _request(collections=[], collection_links=links, local_output=str(tmp_path / "stac-final")),
        default_username="oidc-user",
    )
    final = tmp_path / "stac-final"
    assert payload["status"] == "completed"
    catalog = json.loads((final / "catalog.json").read_text())
    assert {link["href"] for link in catalog["links"] if link["rel"] == "child"} == {
        "collections/one/collection.json",
        "collections/two/collection.json",
    }
    assert (final / "collections" / "one" / "item-one" / "item-one.json").exists()
    assert (final / "collections" / "two" / "item-two" / "item-two.json").exists()
    item = json.loads((final / "collections" / "two" / "item-two" / "item-two.json").read_text())
    assert item["collection"] == "two"
    assert {link["href"] for link in item["links"] if link["rel"] == "root"} == {"../../../catalog.json"}


@unit
def test_collection_links_skip_bad_links_with_warnings(tmp_path):
    source = tmp_path / "collections"
    _tree(str(source), ("one", ["item-one"]))
    payload = suggest_catalog(
        _request(
            collections=[],
            collection_links=[str(source / "one" / "collection.json"), str(source / "missing" / "collection.json")],
            local_output=str(tmp_path / "stac-final"),
        ),
        default_username="oidc-user",
    )
    assert payload["exported"] is True
    assert any("missing" in warning and "skipped" in warning for warning in payload["warnings"])
    final = tmp_path / "stac-final"
    assert (final / "collections" / "one" / "collection.json").exists()
    assert not (final / "collections" / "missing").exists()


@unit
def test_assemble_rejects_missing_folder_and_duplicate_ids(tmp_path):
    with pytest.raises(FileNotFoundError):
        suggest_catalog(_request(collections=[], source_folder=str(tmp_path / "nope"), local_output=str(tmp_path / "x")))

    source = tmp_path / "collections"
    same_dir = source / "flat"
    same_dir.mkdir(parents=True)
    (same_dir / "a.json").write_text(json.dumps(_collection("one")))
    (same_dir / "b.json").write_text(json.dumps(_collection("two")))
    with pytest.raises(ValueError, match="share this directory"):
        suggest_catalog(
            _request(collections=[], source_folder=str(source), local_output=str(tmp_path / "x"))
        )


@unit
def test_assemble_skips_stray_and_invalid_items_with_warnings(tmp_path):
    source = tmp_path / "collections"
    _tree(str(source), ("one", ["item-one"]), ("two", ["item-two"]))
    (source / "one" / "item-one" / "broken.json").write_text("{not json")
    stray = {"type": "Feature", "id": "lost"}
    (source / "stray.json").write_text(json.dumps(stray))

    payload, final = _assemble(tmp_path)
    assert not (final / "collections" / "one" / "item-one" / "broken.json").exists()
    assert not (final / "stray.json").exists()
    assert any("stray.json" in warning and "not inside any collection" in warning for warning in payload["warnings"])


@unit
def test_assemble_overwrite_guards_catalog_files(tmp_path):
    _, final = _assemble(tmp_path)
    with pytest.raises(FileExistsError, match="overwrite=true"):
        _assemble(tmp_path)
    payload, _ = _assemble(tmp_path, overwrite=True)
    assert payload["exported"] is True


@unit
def test_request_rejects_assemble_boundaries(tmp_path):
    source = tmp_path / "collections"
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _request(collections=[_collection("one")], source_folder=str(source), local_output=str(tmp_path / "x"))
    with pytest.raises(ValidationError, match="always exports"):
        _request(collections=[], source_folder=str(source))
    with pytest.raises(ValidationError, match="collections, collection_links, source_folder"):
        _request(collections=[])
    with pytest.raises(ValidationError, match="non-empty"):
        _request(collections=[], collection_links=[""], base={"type": "Catalog", "id": "b"})
    with pytest.raises(ValidationError, match="Missing S3 connection fields"):
        _request(collections=[], base={"type": "Catalog", "id": "b"}, endpoint_url="https://s3.example.com", access_key_id="k")
