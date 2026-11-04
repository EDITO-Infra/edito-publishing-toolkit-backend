"""Steps 3+4 of the collection suggestion: attach the items and write the folder.

Two export shapes:

- ``export_tree`` — item_links/inline items plus a ``target``: the collection
  and its items are saved as a pystac tree (``collection.json`` plus
  ``<item-id>/<item-id>.json``, item ids slugified when unsafe) into a local
  folder, or staged locally and uploaded to an ``s3://bucket/prefix``.
- ``export_into_source_folder`` — a ``source_folder``: ``collection.json`` is
  written inside the folder and the item files are re-linked in place; nothing
  is moved or copied.

No FastAPI and no EDITO policy here.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import pystac

from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.item import validate_item
from ept.infrastructure.formats.stac.pystac_export import assign_safe_hrefs
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    is_s3_uri,
    put_to_s3,
    resolve_s3_storage_options,
    s3_connect,
    s3_url_to_fs,
)

logger = logging.getLogger(__name__)

_OBJECT_FILENAME = "collection.json"


def read_folder_items(
    folder: str, s3: S3ConnectionParameters | None
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Read the ``*.json`` items under ``folder`` (recursive); non-items warn and are skipped.

    Every kept item is schema-validated. Returns the item dicts, their paths
    relative to the folder (same order), and the skip warnings.
    """

    if is_s3_uri(folder):
        fs, root = s3_url_to_fs(folder, storage_options=_storage_options(s3))
        root = root.rstrip("/")
        entries = sorted(
            str(path) for path in fs.find(root, withdirs=False) if str(path).lower().endswith(".json")
        )
        contents = [_read_s3_json(fs, path) for path in entries]
        rel_names = [PurePosixPath(path).relative_to(root).as_posix() for path in entries]
    else:
        base = Path(folder)
        if not base.is_dir():
            raise FileNotFoundError(f"source_folder not found or not a directory: {folder}")
        entries = sorted(base.rglob("*.json"))
        contents = [_read_local_json(path) for path in entries]
        rel_names = [path.relative_to(base).as_posix() for path in entries]

    items: list[dict[str, Any]] = []
    names: list[str] = []
    warnings: list[str] = []
    for rel, data in zip(rel_names, contents, strict=True):
        if data is None:
            warnings.append(f"{rel}: skipped unreadable JSON file.")
            continue
        if data.get("type") != "Feature":
            warnings.append(f"{rel}: skipped, not a STAC Item (type != 'Feature').")
            continue
        try:
            validate_item(data)
        except ValueError as exc:
            raise ValueError(f"{rel} failed STAC Item validation: {exc}") from exc
        logger.info("Step 1: read valid item id=%s from %s", data.get("id"), rel)
        items.append(data)
        names.append(rel)
    if not items:
        raise ValueError(f"No STAC Items found under source_folder: {folder}")
    return items, names, warnings


def export_tree(
    collection_dict: dict[str, Any],
    items: list[dict[str, Any]],
    folder: str,
    *,
    overwrite: bool,
    s3: S3ConnectionParameters | None,
    warnings: list[str],
) -> tuple[dict[str, Any], str, list[str]]:
    """Save the collection and its items as a pystac tree into ``folder``.

    Standard self-contained layout: ``collection.json`` at the root, each
    item under ``<item-id>/<item-id>.json`` (item ids slugified when unsafe,
    with a warning; see :mod:`ept.infrastructure.formats.stac.pystac_export`).
    Returns the saved collection JSON and its URI, plus the written item file
    URIs.
    """

    collection = pystac.Collection.from_dict(collection_dict)
    for item in items:
        try:
            validate_item(item)
        except ValueError as exc:
            raise ValueError(f"item '{item.get('id', '<unknown>')}' failed STAC Item validation: {exc}") from exc
        collection.add_item(pystac.Item.from_dict(item))
    logger.info("Step 3: %d item(s) added to the collection", len(items))

    def _save(target: Path) -> None:
        guard = target / _OBJECT_FILENAME
        if not overwrite and guard.exists():
            raise FileExistsError(f"Target already exists: {guard}. Set overwrite=true to replace it.")
        assign_safe_hrefs(collection, target, warnings)
        collection.save(catalog_type=pystac.CatalogType.SELF_CONTAINED)
        logger.info("Step 4: saved collection tree to %s", target)

    if is_s3_uri(folder):
        with tempfile.TemporaryDirectory(prefix="ept-suggest-collection-") as staging:
            export_dir = Path(staging) / "collection"
            _save(export_dir)
            _upload(export_dir, folder, s3, overwrite=overwrite)
            stac_object = json.loads((export_dir / _OBJECT_FILENAME).read_text(encoding="utf-8"))
            uris = [
                f"{folder}/{path.relative_to(export_dir).as_posix()}"
                for path in sorted(export_dir.rglob("*.json"))
            ]
    else:
        export_dir = Path(folder).resolve()
        _save(export_dir)
        stac_object = json.loads((export_dir / _OBJECT_FILENAME).read_text(encoding="utf-8"))
        uris = [str(path) for path in sorted(export_dir.rglob("*.json"))]

    collection_uri = uris.pop(next(index for index, uri in enumerate(uris) if uri.endswith(_OBJECT_FILENAME)))
    return stac_object, collection_uri, uris


def export_into_source_folder(
    collection_dict: dict[str, Any],
    items: list[dict[str, Any]],
    rel_names: list[str],
    folder: str,
    *,
    overwrite: bool,
    s3: S3ConnectionParameters | None,
) -> tuple[dict[str, Any], str, list[str]]:
    """Write collection.json into the source folder and re-link the item files in place.

    Items keep their filenames and locations; each gains the collection id and
    root/parent/collection links pointing at the folder's collection.json.
    """

    collection_id = str(collection_dict["id"])
    for item, rel in zip(items, rel_names, strict=True):
        parent_href = _relative_collection_href(rel)
        item["collection"] = collection_id
        # No 'self' link: the STAC schemas require an absolute IRI for rel='self'.
        item["links"] = [
            {"rel": "root", "href": parent_href},
            {"rel": "parent", "href": parent_href},
            {"rel": "collection", "href": parent_href},
        ]
        validate_item(item)
    logger.info("Step 3: re-linked %d item file(s) in place", len(items))

    collection_dict["links"] = [
        {"rel": "root", "href": _OBJECT_FILENAME},
        *[{"rel": "item", "href": rel} for rel in rel_names],
    ]
    validate_collection(collection_dict)

    payloads = [
        (_OBJECT_FILENAME, json.dumps(collection_dict, indent=2)),
        *[(rel, json.dumps(item, indent=2)) for rel, item in zip(rel_names, items, strict=True)],
    ]
    written = _write_files(folder, payloads, overwrite=overwrite, s3=s3, guard_items=False)
    logger.info("Step 4: wrote collection.json + %d item file(s) into %s", len(items), folder)
    return collection_dict, written[0], written[1:]


def _relative_collection_href(item_rel: str) -> str:
    """Path of collection.json as seen from the item's folder."""

    item_dir = PurePosixPath(item_rel).parent
    depth = len(item_dir.parts)
    return "/".join([".."] * depth + [_OBJECT_FILENAME]) if depth else _OBJECT_FILENAME


def _storage_options(s3: S3ConnectionParameters | None) -> dict[str, object]:
    return resolve_s3_storage_options(s3)


def _read_s3_json(fs: Any, path: str) -> dict[str, Any] | None:
    """Parse one s3:// JSON object; unreadable files are skipped (caller warns)."""
    try:
        with fs.open(path, "r") as handle:
            data = json.loads(handle.read())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Skipped unreadable JSON s3 object %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _read_local_json(path: Path) -> dict[str, Any] | None:
    """Parse one local JSON file; unreadable files are skipped (caller warns)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Skipped unreadable JSON file %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _upload(export_dir: Path, folder: str, s3: S3ConnectionParameters | None, *, overwrite: bool) -> None:
    """Upload every staged file under the s3://bucket/prefix target."""

    fs = s3_connect(s3)
    bucket, _, prefix = folder[len("s3://"):].partition("/")
    prefix = prefix.strip("/")
    for path in sorted(export_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(export_dir).as_posix()
        remote = f"{bucket}/{prefix}/{rel}" if prefix else f"{bucket}/{rel}"
        put_to_s3(fs, path, remote, overwrite=overwrite)
        logger.info("Step 4: uploaded s3://%s", remote)


def _write_files(
    folder: str,
    payloads: list[tuple[str, str]],
    *,
    overwrite: bool,
    s3: S3ConnectionParameters | None,
    guard_items: bool,
) -> list[str]:
    """Write (rel_path, json) pairs into a local folder or s3:// prefix; returns full URIs."""

    if is_s3_uri(folder):
        fs, root = s3_url_to_fs(folder, storage_options=_storage_options(s3))
        root = root.rstrip("/")
        if not overwrite:
            clashes = [
                rel
                for rel, _ in payloads
                if (rel == _OBJECT_FILENAME or guard_items) and fs.exists(f"{root}/{rel}")
            ]
            if clashes:
                raise FileExistsError(
                    f"Target already exists: {folder}/{clashes[0]}. Set overwrite=true to replace it."
                )
        for rel, payload in payloads:
            fs.write_text(f"{root}/{rel}", payload)
        return [f"{folder}/{rel}" for rel, _ in payloads]

    base = Path(folder)
    base.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        clashes = [
            rel for rel, _ in payloads if (rel == _OBJECT_FILENAME or guard_items) and (base / rel).exists()
        ]
        if clashes:
            raise FileExistsError(
                f"Target already exists: {base / clashes[0]}. Set overwrite=true to replace it."
            )
    for rel, payload in payloads:
        (base / rel).write_text(payload, encoding="utf-8")
    return [str(base / rel) for rel, _ in payloads]
