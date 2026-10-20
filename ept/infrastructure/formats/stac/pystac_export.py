"""Safe self-contained pystac tree export.

pystac's ``normalize_hrefs`` derives folder and file names from object ids,
but STAC ids are arbitrary strings. This module assigns the standard
self-contained layout with slugified id-derived names instead — object ids
themselves are never changed.
"""

from __future__ import annotations

from pathlib import Path

import pystac

from ept.infrastructure.storage.s3 import safe_export_name


def assign_safe_hrefs(
    root: pystac.Catalog | pystac.Collection,
    folder: Path,
    warnings: list[str],
) -> None:
    """Assign self-contained hrefs for the whole tree under ``folder``.

    Mirrors pystac's ``normalize_hrefs`` layout — ``catalog.json`` or
    ``collection.json`` per catalog/collection folder, ``<id>/<id>.json`` per
    item — except that ids unsafe as folder/file names are replaced by their
    slugs (with a warning). Two ids mapping to the same export name within
    one folder raise ``ValueError``.
    """

    _assign(root, folder, warnings, {})


def _assign(
    obj: pystac.Catalog | pystac.Collection,
    folder: Path,
    warnings: list[str],
    taken: dict[Path, dict[str, str]],
) -> None:
    def _name(object_id: str, suffix: str) -> str:
        safe = safe_export_name(object_id, fallback="stac")
        if safe != object_id:
            warnings.append(
                f"id '{object_id}' is not safe as a file/folder name; "
                f"exported as '{safe}{suffix}'."
            )
        names = taken.setdefault(folder, {})
        previous = names.get(safe)
        if previous is not None:
            raise ValueError(
                f"Objects '{previous}' and '{object_id}' both map to the export "
                f"name '{safe}' in '{folder}' — give them distinct ids."
            )
        names[safe] = object_id
        return safe

    obj.set_self_href(
        str(folder / ("collection.json" if isinstance(obj, pystac.Collection) else "catalog.json"))
    )
    for child in obj.get_children():
        _assign(child, folder / _name(str(child.id), "/"), warnings, taken)
    for item in obj.get_items():
        safe = _name(str(item.id), ".json")
        item.set_self_href(str(folder / safe / f"{safe}.json"))
