"""STAC Collection construction and tree-export primitives.

Pure STAC-shaped construction/validation/export: no file, S3, or API
knowledge — export functions return ``(relative_path, json_text)`` payloads
the feature layer writes wherever it wants. Features map their request fields
onto ``CollectionInput`` and call ``build_collection`` /
``validate_collection``; overlay and link policy stay with the feature.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

import pystac
from pydantic import BaseModel, Field
from stac_validator.fast_validator import FastValidator

from ept.infrastructure.formats.stac.item import validate_item
from ept.infrastructure.utils.names import safe_export_name

logger = logging.getLogger(__name__)

__all__ = [
    "CollectionInput",
    "build_collection",
    "validate_collection",
    "collection_tree_payloads",
]

_COLLECTION_FILENAME = "collection.json"


class CollectionInput(BaseModel):
    """Strict STAC Collection input expected by ``build_collection``."""

    id: str
    title: str | None = None
    description: str | None = None
    stac_extensions: list[str] = Field(default_factory=list)
    extent: dict[str, Any] | None = None
    license: str | None = None
    providers: list[dict[str, Any]] = Field(default_factory=list)


def build_collection(collection: CollectionInput) -> dict[str, Any]:
    """Build a STAC Collection dict from strict STAC-shaped input."""

    try:
        obj = pystac.Collection(
            id=collection.id,
            description=collection.description or collection.title or collection.id,
            title=collection.title,
            stac_extensions=collection.stac_extensions,
            extent=(
                pystac.Extent.from_dict(collection.extent)
                if collection.extent
                # pystac crashes serializing a Collection without an extent;
                # default to a world-wide open interval and warn instead.
                else pystac.Extent(
                    # Schema-valid world bbox: an empty SpatialExtent([[]]) would
                    # fail the official collection schema (bbox needs numbers).
                    spatial=pystac.SpatialExtent([[-180.0, -90.0, 180.0, 90.0]]),
                    temporal=pystac.TemporalExtent([[None, None]]),
                )
            ),
            license=collection.license or "proprietary",
            providers=[pystac.Provider.from_dict(provider) for provider in collection.providers],
        )
    except (pystac.STACError, KeyError, TypeError) as exc:
        raise ValueError(f"Cannot build STAC Collection: {exc}") from exc

    stac_object = obj.to_dict(include_self_link=False, transform_hrefs=False)
    stac_object["stac_version"] = "1.1.0"
    return stac_object


def validate_collection(collection: dict[str, Any]) -> None:
    """Schema-validate a STAC Collection dict (stac-validator fast path); raise ValueError on failure.

    fastjsonschema against the official core schemas — links are checked as
    data (rel/shape), never resolved.
    """

    validator = FastValidator(stac_file="in-memory", quiet=True)
    # run_dict enriches nested link dicts in place (e.g. method: GET) on a
    # shallow copy — deepcopy so the caller's object stays untouched.
    validator.run_dict(copy.deepcopy(collection))
    if not validator.valid:
        errors = [
            entry["error_message"]
            for report in validator.message
            for entry in report.get("errors", [])
        ]
        raise ValueError(f"Generated STAC Collection failed schema validation: {'; '.join(errors) or 'unknown'}")
    logger.info("Validated STAC Collection id=%s", collection.get("id"))


# ─── Export ─────────────────────────────────────────────────────────────


def collection_tree_payloads(
    collection: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    warnings: list[str],
) -> list[tuple[str, str]]:
    """Self-contained collection tree as ``(relative_path, json_text)`` payloads.

    Standard layout: ``collection.json`` at the root, each item under
    ``<item-id>/<item-id>.json`` — ids unsafe as folder/file names are
    slugified (with a warning); two ids mapping to the same name raise
    ``ValueError``. The collection gains ``root`` (itself) and ``item`` links;
    every item gains the collection id and ``root``/``parent``/``collection``
    links pointing at the collection. Item assets and non-topological links
    are preserved as-is.
    """

    collection_id = str(collection["id"])
    kept_links = _kept_links(collection, {"root", "parent", "item", "self", "child"})

    taken: dict[str, str] = {}
    item_rels: list[str] = []
    item_payloads: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        item_id = str(item.get("id", ""))
        safe = safe_export_name(item_id, fallback="item")
        if safe != item_id:
            warnings.append(f"item id '{item_id}' is not safe as a file/folder name; exported as '{safe}/'.")
        previous = taken.get(safe)
        if previous is not None:
            raise ValueError(f"Items '{previous}' and '{item_id}' both map to the export name '{safe}' — give them distinct ids.")
        taken[safe] = item_id
        item_rel = f"{safe}/{safe}.json"
        item_rels.append(item_rel)
        item_payloads.append((item_rel, item))

    collection_json = dict(collection)
    collection_json["links"] = [
        *kept_links,
        {"rel": "root", "href": _COLLECTION_FILENAME},
        *[{"rel": "item", "href": rel} for rel in item_rels],
    ]
    validate_collection(collection_json)

    payloads: list[tuple[str, str]] = [(_COLLECTION_FILENAME, _dumps(collection_json))]
    for item_rel, item in item_payloads:
        item_dir = item_rel.rsplit("/", 1)[0]
        collection_href = f"../{_COLLECTION_FILENAME}"
        kept_item_links = _kept_links(item, {"root", "parent", "collection", "self"})
        item_json = dict(item)
        item_json["collection"] = collection_id
        item_json["links"] = [
            *kept_item_links,
            {"rel": "root", "href": collection_href},
            {"rel": "parent", "href": collection_href},
            {"rel": "collection", "href": collection_href},
        ]
        validate_item(item_json)
        payloads.append((item_rel, _dumps(item_json)))
    return payloads


def _kept_links(obj: dict[str, Any], drop_rels: set[str]) -> list[dict[str, Any]]:
    """New list of the object's links except the topological rels being rewritten.

    Each link dict is a shallow copy: nested values inside a link are shared
    with the original.
    """

    return [
        dict(link)
        for link in obj.get("links") or []
        if isinstance(link, dict) and link.get("rel") not in drop_rels
    ]


def _dumps(stac_object: dict[str, Any]) -> str:
    return json.dumps(stac_object, indent=2)
