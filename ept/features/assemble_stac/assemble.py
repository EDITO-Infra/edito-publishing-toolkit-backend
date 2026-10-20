"""Loose STAC JSON dicts -> one linked, valid STAC tree, assembled and exported with pystac.

The tree structure is resolved from the objects' own links (parent/child/
item/collection rels), falling back to the item ``collection`` property and
finally to the source folder layout. Derivable fields are filled in from the
items. pystac owns tree construction, link generation, and the self-contained
export. No FastAPI and no EDITO policy here — the feature layer decides auth
and maps these results onto its request/response models.
"""

from __future__ import annotations

import json
import posixpath
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pystac

from ept.infrastructure.formats.stac import (
    StacObjectType,
    geometry_bbox,
    get_stac_object_type,
)
from ept.infrastructure.formats.stac.pystac_export import assign_safe_hrefs

__all__ = ["ExportReport", "ExportedObject", "StacObject", "assemble_tree", "export_tree"]

# Same literal the infra builders (catalog.py/collection.py/item.py) stamp on
# generated objects; used to fill a missing 'stac_version' on loose inputs.
STAC_VERSION = "1.1.0"

_WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]


@dataclass
class StacObject:
    """One parsed STAC JSON file, with its path relative to the source root."""

    path: str
    data: dict[str, Any]

    @property
    def obj_type(self) -> StacObjectType:
        return get_stac_object_type(self.data)

    @property
    def directory(self) -> str:
        return posixpath.dirname(self.path)


@dataclass
class ExportedObject:
    """One file written to the target, with its parsed content."""

    path: str
    object_type: StacObjectType
    object_id: str | None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportReport:
    root_path: str
    exported: list[ExportedObject]
    warnings: list[str] = field(default_factory=list)


# ─── Assembly ───────────────────────────────────────────────────────────────


def assemble_tree(
    objects: list[StacObject],
) -> tuple[pystac.Catalog | pystac.Collection, list[str]]:
    """Assemble loose STAC objects into one pystac tree.

    The root is the shallowest Catalog (preferred) or Collection. Every other
    object attaches to its parent, resolved in this order:

    1. the object's own links — ``parent`` and, for items, ``collection``;
       an item's ``collection`` property (an id) wins over every link;
    2. another object's ``child``/``item`` link pointing at it;
    3. the folder layout — nearest folder-ancestor object.

    Derivable fields are filled in: missing ids and ``stac_version``, item
    bboxes (from geometry), item ``collection`` properties (from their parent),
    and collection extents (from items). Returns the root pystac object and
    non-fatal warnings.
    """
    known = [o for o in objects if o.obj_type is not StacObjectType.UNKNOWN]
    warnings: list[str] = []
    skipped = len(objects) - len(known)
    if skipped:
        warnings.append(f"Skipped {skipped} file(s) that are not STAC objects (no usable 'type').")

    if not known:
        raise ValueError("No STAC objects found among the source files.")

    catalogs = [o for o in known if o.obj_type is StacObjectType.CATALOG]
    pool = catalogs or [o for o in known if o.obj_type is StacObjectType.COLLECTION]
    if not pool:
        raise ValueError("No STAC Catalog or Collection found among the source files.")
    root_obj = min(pool, key=lambda o: (_dir_depth(o.path), o.path))
    if len(pool) > 1:
        warnings.append(f"Multiple candidate roots found; used {root_obj.path} as the root.")

    _fix_identity(known, warnings)

    parent_of = _assign_parents(known, root_obj, warnings)

    children_of: dict[str, list[StacObject]] = defaultdict(list)
    by_path = {o.path: o for o in known}
    for child_path, parent in parent_of.items():
        children_of[parent.path].append(by_path[child_path])

    for obj in known:
        if obj is root_obj:
            continue
        if obj.obj_type is StacObjectType.ITEM:
            _fix_item(obj, parent_of[obj.path], warnings)
        elif obj.obj_type is StacObjectType.COLLECTION:
            _fix_extent(obj, _descendant_items(obj.path, children_of, by_path), warnings)
    if root_obj.obj_type is StacObjectType.COLLECTION:
        _fix_extent(root_obj, _descendant_items(root_obj.path, children_of, by_path), warnings)

    return _build_pystac_tree(known, root_obj, parent_of), warnings


def _dir_depth(path: str) -> int:
    directory = posixpath.dirname(path)
    return 0 if not directory else len(directory.split("/"))


# ─── Parent resolution ──────────────────────────────────────────────────────


def _assign_parents(
    known: list[StacObject],
    root_obj: StacObject,
    warnings: list[str],
) -> dict[str, StacObject]:
    """Map every non-root object path to its parent object."""
    by_path = {o.path: o for o in known}
    by_collection_id = {
        str(o.data["id"]): o
        for o in known
        if o.obj_type is StacObjectType.COLLECTION and o.data.get("id")
    }
    claims = _downward_claims(known, root_obj, by_path, warnings)

    for href in _link_hrefs(root_obj, "parent"):
        if _resolve_href(root_obj, href, by_path) is not None:
            warnings.append(f"{root_obj.path}: root has a resolvable 'parent' link; ignored.")
            break

    parent_of: dict[str, StacObject] = {}
    for obj in known:
        if obj is root_obj:
            continue
        claimed = _upward_parent(obj, by_collection_id, by_path, warnings)
        if claimed is None and obj.path in claims:
            parents = claims[obj.path]
            claimed = max(parents, key=lambda p: (_dir_depth(p.path), p is root_obj))
            if len(parents) > 1:
                others = ", ".join(sorted(p.path for p in parents if p is not claimed))
                warnings.append(
                    f"{obj.path}: claimed as child by {len(parents)} objects; "
                    f"attached to {claimed.path} (also claimed by {others})."
                )
        if claimed is None:
            claimed = _folder_parent(obj, root_obj, by_path, warnings)
        parent_of[obj.path] = claimed
    return parent_of


def _link_hrefs(obj: StacObject, *rels: str) -> list[str]:
    links = obj.data.get("links")
    if not isinstance(links, list):
        return []
    return [
        href
        for link in links
        if isinstance(link, dict)
        and isinstance(link.get("rel"), str)
        and link["rel"] in rels
        and isinstance(href := link.get("href"), str)
        and href
    ]


def _resolve_href(obj: StacObject, href: str, by_path: dict[str, StacObject]) -> StacObject | None:
    """Resolve one link href against the object's directory; None when it is
    absolute (not a source file) or matches no parsed object."""
    if href.lower().startswith(("http://", "https://", "s3://")):
        return None
    resolved = posixpath.normpath(posixpath.join(obj.directory, href))
    resolved = resolved.removeprefix("./")
    for candidate in _href_variants(resolved):
        target = by_path.get(candidate)
        if target is not None and target is not obj:
            return target
    return None


def _href_variants(resolved: str) -> list[str]:
    """Paths to try for one resolved href: as-is, plus directory-style guesses.

    Links like ``./`` or ``some-collection/`` (no extension) may mean the
    collection.json / catalog.json in that directory, or a file named after
    the directory.
    """
    variants = [resolved]
    base = resolved.rstrip("/")
    if base in ("", "."):
        variants += ["collection.json", "catalog.json"]
    elif not posixpath.splitext(base)[1]:
        variants += [
            f"{base}/collection.json",
            f"{base}/catalog.json",
            f"{base}/{posixpath.basename(base)}.json",
        ]
    return variants


def _upward_parent(
    obj: StacObject,
    by_collection_id: dict[str, StacObject],
    by_path: dict[str, StacObject],
    warnings: list[str],
) -> StacObject | None:
    """Parent declared by the object itself: its ``collection`` property, then
    its ``collection``/``parent`` links."""
    if obj.obj_type is StacObjectType.ITEM:
        prop = obj.data.get("collection")
        if prop is not None:
            target = by_collection_id.get(str(prop))
            if target is not None:
                return target
            warnings.append(
                f"{obj.path}: 'collection' property {prop!r} matches no known "
                "collection id; falling back to links and folder layout."
            )
        for href in _link_hrefs(obj, "collection"):
            target = _resolve_href(obj, href, by_path)
            if target is None:
                continue
            if target.obj_type is not StacObjectType.COLLECTION:
                warnings.append(
                    f"{obj.path}: 'collection' link {href!r} targets a "
                    f"{target.obj_type.value}, not a collection."
                )
                continue
            return target
    for href in _link_hrefs(obj, "parent"):
        target = _resolve_href(obj, href, by_path)
        if target is not None:
            return target
    return None


def _downward_claims(
    known: list[StacObject],
    root_obj: StacObject,
    by_path: dict[str, StacObject],
    warnings: list[str],
) -> dict[str, list[StacObject]]:
    """Child path -> objects whose ``child``/``item`` links point at it."""
    claims: dict[str, list[StacObject]] = defaultdict(list)
    for parent in known:
        for rel in ("child", "item"):
            for href in _link_hrefs(parent, rel):
                target = _resolve_href(parent, href, by_path)
                if target is None or target is parent or target is root_obj:
                    continue
                if target.obj_type is StacObjectType.ITEM and rel == "child":
                    warnings.append(
                        f"{parent.path}: 'child' link to item {target.path}; "
                        "items should use rel='item'."
                    )
                claims[target.path].append(parent)
    return claims


def _folder_parent(
    obj: StacObject,
    root_obj: StacObject,
    by_path: dict[str, StacObject],
    warnings: list[str],
) -> StacObject:
    """Nearest object whose directory contains this object's directory.

    Strict folder ancestors always qualify; an item sitting in a collection's
    own folder is that collection's item; the root also adopts objects sitting
    in its own directory (the flat layout). Same-directory siblings never
    parent each other otherwise, and the root wins the tie at equal depth.
    """
    best: StacObject | None = None
    best_depth = -1
    for path, candidate in by_path.items():
        directory = posixpath.dirname(path)
        if candidate.obj_type is StacObjectType.ITEM or (
            directory == obj.directory
            and candidate is not root_obj
            and not (
                obj.obj_type is StacObjectType.ITEM
                and candidate.obj_type is StacObjectType.COLLECTION
            )
        ):
            continue
        if not _contains_dir(directory, obj.directory):
            continue
        depth = _dir_depth(path)
        if depth > best_depth or (depth == best_depth and candidate is root_obj):
            best, best_depth = candidate, depth
    if best is None:
        warnings.append(
            f"{obj.path}: outside the root's folder; attached directly to {root_obj.path}."
        )
        best = root_obj
    return best


def _contains_dir(ancestor: str, directory: str) -> bool:
    if ancestor == directory:
        return True
    if not ancestor:
        return True  # the source root contains every directory
    return directory.startswith(ancestor + "/")


def _descendant_items(
    path: str,
    children_of: dict[str, list[StacObject]],
    by_path: dict[str, StacObject],
) -> list[StacObject]:
    items, stack = [], list(children_of.get(path, []))
    while stack:
        obj = stack.pop()
        if obj.obj_type is StacObjectType.ITEM:
            items.append(obj)
        stack.extend(children_of.get(obj.path, []))
    return [by_path[i.path] for i in items]


def _build_pystac_tree(
    known: list[StacObject],
    root_obj: StacObject,
    parent_of: dict[str, StacObject],
) -> pystac.Catalog | pystac.Collection:
    built: dict[str, pystac.STACObject] = {o.path: _to_pystac(o) for o in known}
    for obj in known:
        if obj is root_obj:
            continue
        parent = built[parent_of[obj.path].path]
        child = built[obj.path]
        if obj.obj_type is StacObjectType.ITEM:
            parent.add_item(child)  # type: ignore[arg-type]
        else:
            parent.add_child(child)  # type: ignore[arg-type]
    return built[root_obj.path]  # type: ignore[return-value]


_STRUCTURAL_RELS = {"root", "parent", "child", "item", "collection"}


def _to_pystac(obj: StacObject) -> pystac.STACObject:
    # Structural links (incl. 'root') were consumed for parenting or are tree
    # metadata; pystac regenerates them from the tree on save. Keeping them
    # would make pystac resolve relative hrefs at attach time, before any
    # self href exists (STACError) — or read a root link through the virtual
    # self href of a not-yet-exported object.
    links = obj.data.get("links")
    data = obj.data
    if isinstance(links, list):
        data = {
            **data,
            "links": [
                link
                for link in links
                if not (isinstance(link, dict) and link.get("rel") in _STRUCTURAL_RELS)
            ],
        }
    try:
        match obj.obj_type:
            case StacObjectType.CATALOG:
                pystac_obj: pystac.STACObject = pystac.Catalog.from_dict(data)
            case StacObjectType.COLLECTION:
                pystac_obj = pystac.Collection.from_dict(data)
            case StacObjectType.ITEM:
                pystac_obj = pystac.Item.from_dict(data)
            case _:
                raise ValueError(f"not a STAC object (type {obj.data.get('type')!r})")
    except Exception as exc:
        raise ValueError(f"{obj.path}: pystac rejected the object: {exc}") from exc
    # Virtual absolute self href so any remaining relative links resolve at
    # attach time; export_tree's normalize_hrefs replaces it with the real
    # target location.
    pystac_obj.set_self_href(f"/{obj.path}")
    return pystac_obj


# ─── Field fix-ups ──────────────────────────────────────────────────────────


def _fix_identity(objects: list[StacObject], warnings: list[str]) -> None:
    """Fill missing 'stac_version' and 'id' before structure-sensitive fix-ups."""
    for obj in objects:
        data = obj.data
        if not data.get("stac_version"):
            data["stac_version"] = STAC_VERSION
            warnings.append(f"{obj.path}: filled missing 'stac_version' with {STAC_VERSION}.")
        if not data.get("id"):
            data["id"] = _derived_id(obj)
            warnings.append(f"{obj.path}: filled missing 'id' with {data['id']!r}.")


def _derived_id(obj: StacObject) -> str:
    """Derive an id from the source file: its folder name, else the file stem."""
    if obj.directory:
        return obj.directory.rsplit("/", 1)[-1]
    return posixpath.splitext(posixpath.basename(obj.path))[0]


def _fix_item(obj: StacObject, parent: StacObject, warnings: list[str]) -> None:
    data = obj.data
    if not data.get("bbox") and data.get("geometry"):
        bbox = geometry_bbox(data["geometry"])
        if bbox:
            data["bbox"] = bbox
            warnings.append(f"{data['id']}: derived 'bbox' from geometry.")
    if parent.obj_type is StacObjectType.COLLECTION:
        parent_id = str(parent.data["id"])
        if data.get("collection") != parent_id:
            if data.get("collection") is not None:
                warnings.append(
                    f"{data['id']}: 'collection' property set to parent collection "
                    f"{parent_id!r}."
                )
            data["collection"] = parent_id


def _fix_extent(
    obj: StacObject,
    items: list[StacObject],
    warnings: list[str],
) -> None:
    extent = obj.data.get("extent") or {}
    spatial = extent.get("spatial") or {}
    temporal = extent.get("temporal") or {}

    if not spatial.get("bbox"):
        bbox = _union_bbox([b for b in (_item_bbox(i.data) for i in items) if b])
        if bbox is None:
            bbox = list(_WORLD_BBOX)
            warnings.append(
                f"{obj.data['id']}: no item bbox available; used world bbox for extent."
            )
        spatial["bbox"] = [bbox]
        warnings.append(f"{obj.data['id']}: derived spatial extent from items.")
    if not temporal.get("interval"):
        interval = _union_interval([i for i in (_item_interval(i.data) for i in items) if i])
        if interval is None:
            interval = [None, None]
            warnings.append(
                f"{obj.data['id']}: no item datetime available; used an open temporal extent."
            )
        temporal["interval"] = [interval]
        warnings.append(f"{obj.data['id']}: derived temporal extent from items.")

    obj.data["extent"] = {"spatial": spatial, "temporal": temporal}


def _item_bbox(data: dict[str, Any]) -> list[float] | None:
    bbox = data.get("bbox")
    if isinstance(bbox, list) and len(bbox) in (4, 6) and all(
        isinstance(v, (int, float)) for v in bbox
    ):
        return [float(v) for v in bbox]
    return geometry_bbox(data.get("geometry"))


def _item_interval(data: dict[str, Any]) -> tuple[str, str] | None:
    properties = data.get("properties") or {}
    instant = properties.get("datetime")
    start = properties.get("start_datetime") or instant
    end = properties.get("end_datetime") or instant
    if start is None or end is None:
        return None
    return (str(start), str(end))


def _union_bbox(bboxes: list[list[float]]) -> list[float] | None:
    usable = [b for b in bboxes if len(b) == 4]
    if not usable:
        return None
    return [
        min(b[0] for b in usable),
        min(b[1] for b in usable),
        max(b[2] for b in usable),
        max(b[3] for b in usable),
    ]


def _union_interval(intervals: list[tuple[str, str]]) -> list[str | None] | None:
    starts = [s for s, _ in intervals if s]
    ends = [e for _, e in intervals if e]
    if not starts and not ends:
        return None
    return [min(starts) if starts else None, max(ends) if ends else None]


# ─── Export ─────────────────────────────────────────────────────────────────


def export_tree(
    root: pystac.Catalog | pystac.Collection,
    target_dir: str,
    *,
    overwrite: bool = True,
    warnings: list[str] | None = None,
) -> ExportReport:
    """Save the pystac tree into ``target_dir`` and report every written file.

    Layout (pystac self-contained): the root object goes to ``catalog.json``
    (or ``collection.json`` for a collection root) at the target root; each
    child goes to ``<child-id>/collection.json``, each item to
    ``<parent-path>/<item-id>/<item-id>.json``. Ids used for folders/file
    names are slugified when unsafe (with a warning); ids themselves are
    never changed, and two ids slugging to the same name in one folder are
    rejected. All internal links are relative. Schema validation is the
    caller's concern — each exported object's parsed content is in the
    report.
    """
    export_path = Path(target_dir).resolve()
    if not overwrite and any(export_path.glob("*.json")):
        raise FileExistsError(f"{export_path} contains STAC JSON and overwrite=false")

    assign_safe_hrefs(root, export_path, warnings if warnings is not None else [])
    root.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
    root_path = Path(root.self_href).relative_to(export_path).as_posix()

    exported: list[ExportedObject] = []
    for path in sorted(export_path.rglob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        exported.append(
            ExportedObject(
                path=path.relative_to(export_path).as_posix(),
                object_type=get_stac_object_type(data),
                object_id=data.get("id"),
                data=data,
            )
        )
    return ExportReport(root_path=root_path, exported=exported)
