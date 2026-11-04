"""STAC Catalog construction and tree-export primitives.

Pure STAC-shaped construction/validation/export: no file, S3, or API
knowledge — export functions return ``(relative_path, json_text)`` payloads
the feature layer writes wherever it wants. Features map their request fields
onto ``CatalogInput`` and call ``build_catalog`` / ``validate_catalog``;
overlay and link policy stay with the feature.
"""

from __future__ import annotations

import copy
import json
import logging
import posixpath
from typing import Any

import pystac
from pydantic import BaseModel, Field
from stac_validator.fast_validator import FastValidator

from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.item import validate_item
from ept.infrastructure.utils.names import safe_export_name

logger = logging.getLogger(__name__)

__all__ = [
    "CatalogInput",
    "build_catalog",
    "validate_catalog",
    "catalog_tree_payloads",
    "CollectionScope",
]

_CATALOG_FILENAME = "catalog.json"
_COLLECTION_FILENAME = "collection.json"

# (collection, [(item_rel, item), ...]) — item_rel is the item's source path
# relative to its collection (kept as the export path when safe).
CollectionScope = tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]


class CatalogInput(BaseModel):
    """Strict STAC Catalog input expected by ``build_catalog``."""

    id: str
    title: str | None = None
    description: str | None = None
    stac_extensions: list[str] = Field(default_factory=list)


def build_catalog(catalog: CatalogInput) -> dict[str, Any]:
    """Build a STAC Catalog dict from strict STAC-shaped input."""

    obj = pystac.Catalog(
        id=catalog.id,
        description=catalog.description or catalog.title or catalog.id,
        title=catalog.title,
        stac_extensions=catalog.stac_extensions,
    )
    stac_object = obj.to_dict(include_self_link=False, transform_hrefs=False)
    stac_object["stac_version"] = "1.1.0"
    return stac_object


def validate_catalog(catalog: dict[str, Any]) -> None:
    """Schema-validate a STAC Catalog dict (stac-validator fast path); raise ValueError on failure.

    fastjsonschema against the official core schemas — links are checked as
    data (rel/shape), never resolved.
    """

    validator = FastValidator(stac_file="in-memory", quiet=True)
    # run_dict enriches nested link dicts in place (e.g. method: GET) on a
    # shallow copy — deepcopy so the caller's object stays untouched.
    validator.run_dict(copy.deepcopy(catalog))
    if not validator.valid:
        errors = [
            entry["error_message"]
            for report in validator.message
            for entry in report.get("errors", [])
        ]
        raise ValueError(f"Generated STAC Catalog failed schema validation: {'; '.join(errors) or 'unknown'}")
    logger.info("Validated STAC Catalog id=%s", catalog.get("id"))


# ─── Export ─────────────────────────────────────────────────────────────


def catalog_tree_payloads(
    catalog: dict[str, Any],
    collection_scopes: list[CollectionScope],
    *,
    warnings: list[str],
) -> list[tuple[str, str]]:
    """One full catalog tree as ``(relative_path, json_text)`` payloads.

    Layout: ``catalog.json`` at the root; each collection scope in its own
    folder next to it (``<collection-id>/collection.json`` plus its item
    files). Every root/parent/item link is rewritten relative to the new
    tree. Ids unsafe as folder/file names are slugified (with a warning); ids
    themselves are never changed. Duplicate collection ids raise
    ``ValueError`` (the feature layer combines same-id sources first).
    """

    _require_unique([collection for collection, _ in collection_scopes], "collection")
    _dirnames([collection for collection, _ in collection_scopes], warnings)

    payloads: list[tuple[str, str]] = []
    child_hrefs: list[str] = []
    for collection, items in collection_scopes:
        collection_rel, collection_payloads = _collection_scope(
            collection,
            items,
            catalog_rel=_CATALOG_FILENAME,
            warnings=warnings,
        )
        child_hrefs.append(collection_rel)
        payloads.extend(collection_payloads)

    catalog_json = dict(catalog)
    catalog_json["links"] = [
        {"rel": "root", "href": _CATALOG_FILENAME},
        *[{"rel": "child", "href": href} for href in child_hrefs],
    ]
    validate_catalog(catalog_json)
    payloads.insert(0, (_CATALOG_FILENAME, _dumps(catalog_json)))
    return payloads


def _collection_scope(
    collection: dict[str, Any],
    items: list[tuple[str, dict[str, Any]]],
    *,
    catalog_rel: str,
    warnings: list[str],
) -> tuple[str, list[tuple[str, str]]]:
    """One collection + its items; returns (collection_rel, payloads)."""

    collection_id = str(collection["id"])
    dest_dir = safe_export_name(collection_id, fallback="collection")
    if dest_dir != collection_id:
        warnings.append(
            f"collection id '{collection_id}' is not safe as a folder name; "
            f"its files are exported under '{dest_dir}/'."
        )
    collection_rel = f"{dest_dir}/{_COLLECTION_FILENAME}"

    safe_item_rels = [
        "/".join(safe_export_name(segment, fallback="item") for segment in item_rel.split("/"))
        for item_rel, _ in items
    ]
    for original, safe in zip((item_rel for item_rel, _ in items), safe_item_rels, strict=True):
        if original != safe:
            warnings.append(
                f"item path '{dest_dir}/{original}' is not safe as an export path; "
                f"exported as '{dest_dir}/{safe}'."
            )

    kept = _kept_links(collection, {"root", "parent", "item", "self", "child"})
    collection_json = dict(collection)
    collection_json["links"] = [
        *kept,
        {"rel": "root", "href": posixpath.relpath(catalog_rel, dest_dir)},
        {"rel": "parent", "href": posixpath.relpath(catalog_rel, dest_dir)},
        *[{"rel": "item", "href": rel} for rel in safe_item_rels],
    ]
    validate_collection(collection_json)

    payloads = [(collection_rel, _dumps(collection_json))]
    for (item_rel, item), safe_rel in zip(items, safe_item_rels, strict=True):
        item_dest_rel = f"{dest_dir}/{safe_rel}"
        item_dir = posixpath.dirname(item_dest_rel)
        collection_href = posixpath.relpath(collection_rel, item_dir)
        kept_item_links = _kept_links(item, {"root", "parent", "collection", "self"})
        item_json = dict(item)
        item_json["collection"] = collection_id
        item_json["links"] = [
            *kept_item_links,
            {"rel": "root", "href": posixpath.relpath(catalog_rel, item_dir)},
            {"rel": "parent", "href": collection_href},
            {"rel": "collection", "href": collection_href},
        ]
        validate_item(item_json)
        payloads.append((item_dest_rel, _dumps(item_json)))
    return collection_rel, payloads


def _require_unique(objects: list[dict[str, Any]], kind: str) -> None:
    ids = [str(obj.get("id", "")) for obj in objects]
    duplicates = {value for value in ids if value and ids.count(value) > 1}
    if duplicates:
        raise ValueError(
            f"Duplicate {kind} id(s) across sources: {', '.join(sorted(duplicates))} — "
            f"each {kind} must appear once."
        )


def _dirnames(objects: list[dict[str, Any]], warnings: list[str]) -> dict[str, str]:
    """Folder name per object id: the id when folder-safe, else its slug.

    Raises when two objects would share one folder (e.g. ids that slugify to
    the same name).
    """

    taken: dict[str, str] = {}
    dirname_of: dict[str, str] = {}
    for obj in objects:
        object_id = str(obj["id"])
        dirname = safe_export_name(object_id, fallback="object")
        if dirname != object_id:
            warnings.append(
                f"id '{object_id}' is not safe as a folder name; "
                f"its files are exported under '{dirname}/'."
            )
        previous = taken.get(dirname)
        if previous is not None:
            raise ValueError(
                f"Objects '{previous}' and '{object_id}' both map to the folder "
                f"'{dirname}/' — give them distinct ids."
            )
        taken[dirname] = object_id
        dirname_of[object_id] = dirname
    return dirname_of


def _kept_links(obj: dict[str, Any], drop_rels: set[str]) -> list[dict[str, Any]]:
    """Copies of the object's links except the topological rels being rewritten."""

    return [
        dict(link)
        for link in obj.get("links") or []
        if isinstance(link, dict) and link.get("rel") not in drop_rels
    ]


def _dumps(stac_object: dict[str, Any]) -> str:
    return json.dumps(stac_object, indent=2)
