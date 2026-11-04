"""Unit tests for the stac_collection_tree feature: derivation and boundaries."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ept.features.stac_collection_tree.models import CreateCollectionTreeRequest
from ept.features.stac_collection_tree.service import _union_bbox, create_collection_tree
from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.item import validate_item
from tests.conftest import unit


def _item(item_id: str, bbox: list[float], datetime_: str | None = None, **props) -> dict:
    # STAC 1.1: the `datetime` key is required — null for interval items.
    properties = {"datetime": datetime_, **props} if datetime_ else {
        "datetime": None, **props
    }
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "id": item_id,
        "bbox": bbox,
        "geometry": {"type": "Point", "coordinates": [bbox[0], bbox[1]]},
        "properties": properties,
        "assets": {},
        "links": [],
    }


def _request(**overrides) -> CreateCollectionTreeRequest:
    values = {
        "items": [
            _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
            _item("b", [2.0, -1.0, 3.0, 2.0], start_datetime="2025-02-01T00:00:00Z", end_datetime="2025-03-01T00:00:00Z"),
        ],
        "title": "North Sea Observations",
    }
    values.update(overrides)
    return CreateCollectionTreeRequest(**values)


@unit
def test_request_rejects_bad_inputs():
    with pytest.raises(ValidationError, match="items, item_links, source_prefix, or a base"):
        CreateCollectionTreeRequest()
    with pytest.raises(ValidationError, match="type == 'Feature'"):
        _request(items=[{"type": "Catalog", "id": "x"}])
    with pytest.raises(ValidationError, match="type == 'Collection'"):
        _request(items=[], base={"type": "Catalog", "id": "x"})
    with pytest.raises(ValidationError, match="must have an id"):
        _request(items=[], base={"type": "Collection"})
    with pytest.raises(ValidationError, match="flat"):
        _request(id="a/../b")
    with pytest.raises(ValidationError, match="flat"):
        _request(id="a/b")


@unit
def test_extent_is_union_of_item_bboxes_and_datetimes():
    payload = create_collection_tree(_request(), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["extent"]["spatial"]["bbox"] == [[0.0, -1.0, 3.0, 2.0]]
    assert obj["extent"]["temporal"]["interval"] == [["2025-01-01T00:00:00Z", "2025-03-01T00:00:00Z"]]
    assert payload["status"] == "preview"


@unit
def test_union_bbox_projects_mixed_dimensions():
    mixed = [[0.0, 1.0, 2.0, 3.0], [-1.0, -2.0, 0.0, 500.0, 4.0, 600.0]]
    assert _union_bbox(mixed) == [-1.0, -2.0, 500.0, 4.0]


@unit
def test_id_rules_explicit_base_username_error():
    explicit = create_collection_tree(_request(id="my-collection"), default_username="oidc-user")
    assert explicit["stac_object"]["id"] == "my-collection"

    from_base = create_collection_tree(_request(base={"type": "Collection", "id": "projects/oidc-user/base"}))
    assert from_base["stac_object"]["id"] == "base-north-sea-observations"

    from_username = create_collection_tree(_request(), default_username="oidc-user")
    assert from_username["stac_object"]["id"] == "oidc-user-north-sea-observations"

    with pytest.raises(ValueError, match="Cannot propose a collection id"):
        create_collection_tree(_request(), default_username=None)


@unit
def test_license_defaults_other_then_item_then_base_then_request():
    # No license anywhere -> generic default.
    assert create_collection_tree(_request(), default_username="oidc-user")["stac_object"]["license"] == "other"

    # Shared item license is promoted; agreeing across shapes (top-level + properties).
    items = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
        {**_item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"), "license": "CC-BY-4.0"},
        _item("c", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z", license="CC-BY-4.0"),
    ]
    payload = create_collection_tree(_request(items=items), default_username="oidc-user")
    assert payload["stac_object"]["license"] == "CC-BY-4.0"
    assert any("derived from the items" in warning for warning in payload["warnings"])

    # Conflicting item licenses are reported and fall back to the default.
    conflicting = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z", license="CC-BY-4.0"),
        _item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z", license="CC0-1.0"),
    ]
    payload = create_collection_tree(_request(items=conflicting), default_username="oidc-user")
    assert payload["stac_object"]["license"] == "other"
    assert any("conflicting licenses" in warning for warning in payload["warnings"])


@unit
def test_license_recovered_from_iso_fields():
    # ISO-derived shapes: properties.licenses[].id and constraints prose
    # ("Creative Commons CC-BY 4.0" carries no explicit license field).
    iso_items = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
        _item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"),
    ]
    iso_items[0]["properties"]["licenses"] = [{"id": "CC-BY-4.0"}]
    iso_items[1]["properties"]["constraints"] = [
        {"type": "other_constraints", "text": "Creative Commons CC-BY 4.0 https://creativecommons.org/licenses/by/4.0/"},
        {"type": "other_constraints", "text": "This data was downloaded from the EMODnet Portal."},
    ]
    payload = create_collection_tree(_request(items=iso_items), default_username="oidc-user")
    assert payload["stac_object"]["license"] == "CC-BY-4.0"
    assert any("derived from the items" in warning for warning in payload["warnings"])

    # Base overrides items; explicit request overrides base.
    base = {"type": "Collection", "id": "projects/oidc-user/base", "license": "CC0-1.0"}
    from_base = create_collection_tree(_request(items=iso_items, base=base), default_username="oidc-user")
    assert from_base["stac_object"]["license"] == "CC0-1.0"
    explicit = create_collection_tree(_request(items=iso_items, base=base, license="MIT"), default_username="oidc-user")
    assert explicit["stac_object"]["license"] == "MIT"


@unit
def test_base_metadata_carried_and_extent_recomputed():
    base = {
        "type": "Collection",
        "stac_version": "1.0.0",
        "id": "projects/oidc-user/base",
        "title": "Old title",
        "description": "Old description",
        "license": "CC-BY-4.0",
        "providers": [{"name": "VLIZ", "roles": ["producer"]}],
        "extent": {"spatial": {"bbox": [[-180.0, -90.0, 180.0, 90.0]]}, "temporal": {"interval": [[None, None]]}},
        "links": [],
    }
    payload = create_collection_tree(_request(base=base))
    obj = payload["stac_object"]
    assert obj["license"] == "CC-BY-4.0"
    assert obj["providers"] == [{"name": "VLIZ", "roles": ["producer"]}]
    assert obj["description"] == "Old description"
    # Extent recomputed from items, not the base's world placeholder.
    assert obj["extent"]["spatial"]["bbox"] == [[0.0, -1.0, 3.0, 2.0]]


@unit
def test_providers_collected_from_items_and_merged_with_base():
    # Providers are optional on Items (only `name` required); duplicates on
    # name+url collapse, first-seen wins.
    items = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
        _item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"),
    ]
    items[0]["properties"]["providers"] = [{"name": "VLIZ", "roles": ["producer"]}]
    items[1]["properties"]["providers"] = [{"name": "VLIZ"}, {"name": "EMODnet", "url": "https://emodnet.ec.europa.eu"}]
    payload = create_collection_tree(_request(items=items), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["providers"] == [
        {"name": "VLIZ", "roles": ["producer"]},
        {"name": "EMODnet", "url": "https://emodnet.ec.europa.eu"},
    ]
    assert any("providers merged from the items" in warning for warning in payload["warnings"])

    # Base providers win for duplicates; only item-derived additions warn.
    base = {"type": "Collection", "id": "projects/oidc-user/base", "providers": [{"name": "VLIZ"}]}
    merged = create_collection_tree(_request(items=items, base=base), default_username="oidc-user")
    assert merged["stac_object"]["providers"] == [
        {"name": "VLIZ"},
        {"name": "EMODnet", "url": "https://emodnet.ec.europa.eu"},
    ]

    # Base-only providers are carried without a derivation warning.
    base_only = create_collection_tree(_request(base=base), default_username="oidc-user")
    assert base_only["stac_object"]["providers"] == [{"name": "VLIZ"}]
    assert not any("providers merged" in warning for warning in base_only["warnings"])


@unit
def test_extent_falls_back_to_base_then_placeholder_with_warnings():
    no_geometry_items = [
        {"type": "Feature", "stac_version": "1.0.0", "id": "x", "geometry": None, "properties": {}, "assets": {}}
    ]
    base = {
        "type": "Collection",
        "id": "b",
        "extent": {"spatial": {"bbox": [[1.0, 2.0, 3.0, 4.0]]}, "temporal": {"interval": [[None, None]]}},
    }
    from_base = create_collection_tree(_request(items=no_geometry_items, base=base))
    assert from_base["stac_object"]["extent"] == base["extent"]
    assert any("copied from the base" in warning for warning in from_base["warnings"])

    placeholder = create_collection_tree(_request(items=no_geometry_items), default_username="u")
    assert placeholder["stac_object"]["extent"]["spatial"]["bbox"] == [[-180.0, -90.0, 180.0, 90.0]]
    assert placeholder["stac_object"]["extent"]["temporal"]["interval"] == [[None, None]]
    assert any("placeholder" in warning for warning in placeholder["warnings"])


@unit
def test_unparseable_datetime_warns_and_is_ignored():
    payload = create_collection_tree(
        CreateCollectionTreeRequest(
            items=[
                _item("a", [0.0, 0.0, 1.0, 1.0], "not-a-date"),
                _item("b", [2.0, 2.0, 3.0, 3.0], "2025-06-01T00:00:00Z"),
            ]
        ),
        default_username="u",
    )
    assert any("unparseable datetime" in warning for warning in payload["warnings"])
    assert payload["stac_object"]["extent"]["temporal"]["interval"][0][0] == "2025-06-01T00:00:00Z"


@unit
def test_naive_datetimes_treated_as_utc():
    payload = create_collection_tree(
        CreateCollectionTreeRequest(items=[_item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00")]),
        default_username="u",
    )
    interval = payload["stac_object"]["extent"]["temporal"]["interval"]
    assert interval == [["2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"]]


@unit
def test_schema_valid_output_with_defaults_and_no_links():
    payload = create_collection_tree(_request(), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["type"] == "Collection"
    assert obj["stac_version"] == "1.1.0"
    assert obj["license"] == "other"
    assert obj["links"] == []
    assert any("links are empty" in warning for warning in payload["warnings"])


@unit
def test_item_links_are_fetched_and_derived(tmp_path):
    link = tmp_path / "item-a.json"
    link.write_text(
        json.dumps(
            _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z", license="CC-BY-4.0")
        ),
        encoding="utf-8",
    )
    payload = create_collection_tree(
        _request(items=[], item_links=[str(link)], title="Linked"), default_username="oidc-user"
    )
    obj = payload["stac_object"]
    assert obj["license"] == "CC-BY-4.0"
    bbox = obj["extent"]["spatial"]["bbox"][0]
    assert bbox == [0.0, 0.0, 1.0, 1.0]
    assert obj["id"] == "oidc-user-linked"


@unit
def test_unreadable_links_warn_and_are_skipped(tmp_path):
    base = {"type": "Collection", "id": "projects/oidc-user/base", "license": "CC0-1.0"}
    payload = create_collection_tree(
        _request(items=[], item_links=[str(tmp_path / "missing.json")], title="Gone", base=base),
        default_username="oidc-user",
    )
    assert any("skipped" in warning for warning in payload["warnings"])
    assert payload["stac_object"]["license"] == "CC0-1.0"


@unit
def test_all_links_unreadable_and_no_base_fails(tmp_path):
    with pytest.raises(ValueError, match="None of the item_links"):
        create_collection_tree(
            _request(items=[], item_links=[str(tmp_path / "missing.json")]), default_username="oidc-user"
        )


@unit
def test_multiple_distinct_item_licenses_become_a_list():
    items = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z", license="CC-BY-4.0"),
        _item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z", license="ODbL-1.0"),
    ]
    payload = create_collection_tree(_request(items=items), default_username="oidc-user")
    obj = payload["stac_object"]
    assert obj["licenses"] == ["CC-BY-4.0", "ODbL-1.0"]
    assert obj["license"] == "other"
    assert any("multiple licenses" in warning for warning in payload["warnings"])


@unit
def test_keywords_are_unioned_from_items_and_base():
    items = [
        _item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
        _item("b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"),
    ]
    items[0]["properties"]["keywords"] = ["shipwrecks", "heritage"]
    items[1]["properties"]["keywords"] = ["heritage", "emodnet"]
    base = {"type": "Collection", "id": "projects/oidc-user/base", "keywords": ["marine"]}
    payload = create_collection_tree(
        _request(items=items, base=base), default_username="oidc-user"
    )
    assert payload["stac_object"]["keywords"] == ["marine", "shipwrecks", "heritage", "emodnet"]


# ── source_prefix mode: items from a folder, collection written into it ──────


def _write_items(folder, *items: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for item in items:
        (folder / f"{item['id']}.json").write_text(json.dumps(item), encoding="utf-8")


@unit
def test_source_prefix_writes_collection_and_relinks_items_in_place(tmp_path):
    folder = tmp_path / "collection"
    _write_items(
        folder,
        _item("it-1", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
        _item("it-2", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"),
    )
    (folder / "notes.json").write_text('{"not": "a stac item"}', encoding="utf-8")

    payload = create_collection_tree(
        _request(items=[], item_links=[], source_prefix=str(folder), id="obs", title="Obs"),
        default_username="oidc-user",
    )

    assert payload["status"] == "completed"
    assert payload["exported"] is True
    assert payload["collection_uri"] == str(folder / "collection.json")

    collection = json.loads((folder / "collection.json").read_text(encoding="utf-8"))
    assert collection["id"] == "obs"
    assert collection["extent"]["spatial"]["bbox"] == [[0.0, -1.0, 3.0, 2.0]]
    assert {link["href"] for link in collection["links"] if link["rel"] == "item"} == {
        "it-1.json",
        "it-2.json",
    }
    assert {link["href"] for link in collection["links"] if link["rel"] == "root"} == {"collection.json"}

    item = json.loads((folder / "it-1.json").read_text(encoding="utf-8"))
    assert item["collection"] == "obs"
    item_links = {link["rel"]: link["href"] for link in item["links"]}
    assert item_links == {
        "root": "collection.json",
        "parent": "collection.json",
        "collection": "collection.json",
    }
    # the non-item file is skipped with a warning
    assert any("notes.json" in warning for warning in payload["warnings"])


@unit
def test_source_prefix_requires_items(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    with pytest.raises(ValueError, match="No STAC Items found"):
        create_collection_tree(
            _request(items=[], item_links=[], source_prefix=str(folder), id="x"),
            default_username="oidc-user",
        )


@unit
def test_source_prefix_invalid_item_fails(tmp_path):
    folder = tmp_path / "bad"
    folder.mkdir()
    (folder / "it-1.json").write_text(json.dumps({"type": "Feature", "id": "it-1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="failed STAC Item validation"):
        create_collection_tree(
            _request(items=[], item_links=[], source_prefix=str(folder), id="x"),
            default_username="oidc-user",
        )


# ── target export: item_links fetched, collection tree written ───────────────


@unit
def test_item_links_with_target_export_collection_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ept.features.stac_collection_tree.service.fetch_json",
        lambda uri: _item("it-1", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
    )
    target = tmp_path / "collections" / "obs"
    payload = create_collection_tree(
        _request(
            items=[],
            item_links=["https://example.com/it-1.json"],
            id="obs",
            title="Obs",
            local_output=str(target),
        ),
        default_username="oidc-user",
    )

    assert payload["status"] == "completed"
    assert payload["collection_uri"] == str(target / "collection.json")
    # self-contained layout: one <id>/<id>.json folder per item
    assert payload["item_uris"] == [str(target / "it-1" / "it-1.json")]

    collection = json.loads((target / "collection.json").read_text(encoding="utf-8"))
    assert collection["id"] == "obs"
    assert {link["href"] for link in collection["links"] if link["rel"] == "item"} == {"it-1/it-1.json"}
    item = json.loads((target / "it-1" / "it-1.json").read_text(encoding="utf-8"))
    assert item["collection"] == "obs"
    assert {link["rel"] for link in item["links"]} == {"root", "parent", "collection"}


@unit
def test_export_slugs_unsafe_item_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ept.features.stac_collection_tree.service.fetch_json",
        lambda uri: _item("My Item!", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
    )
    target = tmp_path / "obs"
    payload = create_collection_tree(
        _request(
            items=[],
            item_links=["https://example.com/x.json"],
            id="obs",
            title="Obs",
            local_output=str(target),
        ),
        default_username="oidc-user",
    )

    assert payload["item_uris"] == [str(target / "my-item" / "my-item.json")]
    assert any("not safe as a file/folder name" in warning for warning in payload["warnings"])
    item = json.loads((target / "my-item" / "my-item.json").read_text(encoding="utf-8"))
    assert item["id"] == "My Item!"  # id unchanged; only the export name slugged
    collection = json.loads((target / "collection.json").read_text(encoding="utf-8"))
    assert {link["href"] for link in collection["links"] if link["rel"] == "item"} == {"my-item/my-item.json"}


@unit
def test_export_rejects_slug_collisions(tmp_path):
    with pytest.raises(ValueError, match="both map to the export name"):
        create_collection_tree(
            _request(
                items=[
                    _item("a b", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z"),
                    _item("a-b", [2.0, -1.0, 3.0, 2.0], "2025-02-01T00:00:00Z"),
                ],
                id="obs",
                title="Obs",
                local_output=str(tmp_path / "obs"),
            ),
            default_username="oidc-user",
        )


@unit
def test_s3_export_returns_full_https_urls_when_endpoint_is_known(monkeypatch):
    """s3:// export targets render as full https:// URLs when endpoint_url is given."""

    class FakeFS:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            pass

    monkeypatch.setattr(
        "ept.features.stac_collection_tree.export.s3_url_to_fs",
        lambda uri, storage_options=None: (FakeFS(), uri),
    )

    payload = create_collection_tree(
        _request(
            id="obs",
            title="Obs",
            target="collections/obs",
            bucket="project-bucket",
            endpoint_url="https://minio.example.org",
            access_key_id="key",
            secret_access_key="secret",
        ),
        default_username="oidc-user",
    )
    assert payload["collection_uri"] == "https://minio.example.org/project-bucket/collections/obs/collection.json"
    assert payload["item_uris"] == [
        "https://minio.example.org/project-bucket/collections/obs/a/a.json",
        "https://minio.example.org/project-bucket/collections/obs/b/b.json",
    ]


@unit
def test_s3_export_falls_back_to_s3_uris_without_endpoint(monkeypatch):
    class FakeFS:
        def exists(self, path):
            return False

        def write_text(self, path, payload):
            pass

    monkeypatch.setattr(
        "ept.features.stac_collection_tree.export.s3_url_to_fs",
        lambda uri, storage_options=None: (FakeFS(), uri),
    )
    monkeypatch.setattr(
        "ept.features.stac_collection_tree.export.resolve_s3_storage_options",
        lambda params=None: {},
    )

    payload = create_collection_tree(
        _request(id="obs", title="Obs", target="collections/obs", bucket="project-bucket"),
        default_username="oidc-user",
    )
    assert payload["collection_uri"] == "s3://project-bucket/collections/obs/collection.json"
    assert payload["item_uris"] == [
        "s3://project-bucket/collections/obs/a/a.json",
        "s3://project-bucket/collections/obs/b/b.json",
    ]


@unit
def test_target_export_refuses_existing_collection_without_overwrite(tmp_path):
    target = tmp_path / "col"
    target.mkdir()
    (target / "collection.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError, match="overwrite=true"):
        create_collection_tree(
            _request(title="Obs", local_output=str(target)),
            default_username="oidc-user",
        )


@unit
def test_exported_folder_round_trips_as_a_source_prefix(tmp_path):
    """A target-exported collection tree re-reads as a valid source_prefix."""

    from ept.features.stac_collection_tree.export import read_prefix_items

    tree = tmp_path / "stac"
    (tree / "collections").mkdir(parents=True)
    create_collection_tree(
        _request(
            id="obs",
            title="Obs",
            local_output=str(tree / "collections" / "obs"),
        ),
        default_username="oidc-user",
    )
    items, rel_names, warnings = read_prefix_items(str(tree / "collections" / "obs"), None)
    assert warnings == ["collection.json: skipped, not a STAC Item (type != 'Feature')."]
    assert [item["id"] for item in items] == ["a", "b"]
    assert rel_names == ["a/a.json", "b/b.json"]
    # every exported file is schema-valid
    for path in sorted(tree.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("type") == "Feature":
            validate_item(data)
        elif data.get("type") == "Collection":
            validate_collection(data)


# ── request model: source_prefix exclusivity and S3 credentials ──────────────


@unit
def test_source_prefix_is_mutually_exclusive_with_other_sources_and_export_fields():
    with pytest.raises(ValidationError, match="mutually exclusive with items"):
        _request(items=[_item("a", [0.0, 0.0, 1.0, 1.0], "2025-01-01T00:00:00Z")], source_prefix="./col")
    with pytest.raises(ValidationError, match="mutually exclusive with item_links"):
        _request(items=[], item_links=["https://example.com/it.json"], source_prefix="./col")
    with pytest.raises(ValidationError, match="mutually exclusive with target"):
        _request(items=[], item_links=[], source_prefix="./col", target="elsewhere")


@unit
def test_source_prefix_rejects_http_urls():
    with pytest.raises(ValidationError, match="item_links"):
        _request(items=[], item_links=[], source_prefix="https://example.com/items/")


@unit
def test_partial_s3_credentials_rejected():
    with pytest.raises(ValidationError, match="Missing S3 connection fields"):
        _request(endpoint_url="https://minio.example.org")


@unit
def test_preview_requests_do_not_export():
    payload = create_collection_tree(_request(), default_username="oidc-user")
    assert payload["exported"] is False
    assert payload["collection_uri"] is None
    assert payload["item_uris"] == []
    assert payload["status"] == "preview"
