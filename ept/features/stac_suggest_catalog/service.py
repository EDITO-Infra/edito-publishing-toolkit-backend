"""Use-case logic for the STAC catalog-suggestion feature.

Two modes:

- **Preview** — inline ``collections``, fetched ``collection_links``, and/or a
  ``base``: pure metadata. Catalogs carry no extent; the suggestion is
  identity + metadata, enriched from the collections (keywords unioned,
  providers merged). Child links are wired at export time because the child
  hrefs don't exist yet.
- **Assemble** — collection trees from ``source_folder`` (subfolders of
  self-contained Collection trees, the layout ``suggest/collection`` exports)
  and/or ``collection_links`` (each collection's item files are fetched via
  its ``item`` links). Every collection tree is copied under
  ``<target>/<collection-id>/`` with its links re-pointed at the new catalog,
  item copies re-linked, and ``catalog.json`` written at the target root with
  relative child links — a complete, publishable, self-contained tree,
  validatable with /v1/stac/validate (``validate_children``).
"""

from __future__ import annotations

import json
import logging
import posixpath
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ept.infrastructure.formats.stac.catalog import validate_catalog
from ept.infrastructure.formats.stac.collection import validate_collection
from ept.infrastructure.formats.stac.fetch import fetch_json
from ept.infrastructure.formats.stac.item import validate_item
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from ept.infrastructure.storage.s3 import (
    full_s3_url,
    is_s3_uri,
    resolve_s3_storage_options,
    s3_endpoint_url,
    s3_url_to_fs,
    safe_export_name,
    slugify,
)
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import SuggestCatalogRequest

logger = logging.getLogger(__name__)

_CATALOG_FILENAME = "catalog.json"
_COLLECTION_FILENAME = "collection.json"

# A scope: one collection plus its item files as (rel_path_within_tree, item_dict).
Scope = tuple[dict[str, Any], list[tuple[str, dict[str, Any]]]]


def suggest_catalog(
    request: SuggestCatalogRequest,
    default_username: str | None = None,
    default_bucket: str | None = None,
) -> dict[str, Any]:
    """Suggest a STAC Catalog from collections; with a tree source, assemble and export it."""

    warnings: list[str] = []
    s3 = _s3_connection(request)

    destination = request.target or request.local_output
    export = request.source_folder is not None or (bool(request.collection_links) and destination is not None)
    target: str | None = None
    if export:
        if destination is None:  # unreachable via the model validators (source_folder requires a target)
            raise ValueError("source_folder always exports: provide `target` or `local_output`.")
        target = _resolve_target(
            destination,
            local_output=request.local_output,
            bucket=request.bucket or default_bucket,
        )

    scopes: list[Scope] = []
    if request.source_folder is not None:
        scopes.extend(_read_collection_folder(request.source_folder, s3, warnings))
    if request.collection_links:
        scopes.extend(_fetch_collection_links(request.collection_links, warnings))

    stac_object = _derive_catalog(
        request,
        [collection for collection, _ in scopes] + list(request.collections),
        default_username,
        warnings,
    )

    if not export:
        logger.info(
            "Suggested STAC Catalog id=%s collections=%d base=%s exported=false warnings=%d",
            stac_object["id"],
            len(scopes) + len(request.collections),
            bool(request.base),
            len(warnings),
        )
        return {
            "stac_object": stac_object,
            "warnings": warnings,
            "status": "suggested",
            "exported": False,
            "catalog_uri": None,
            "collection_uris": [],
            "item_uris": [],
        }

    stac_object, catalog_uri, collection_uris, item_uris = _export_catalog_tree(
        stac_object,
        scopes,
        target,  # type: ignore[arg-type]  # export implies target
        warnings=warnings,
        overwrite=request.overwrite,
        s3=s3,
    )
    endpoint = s3_endpoint_url(s3) or ""
    logger.info(
        "Assembled STAC Catalog id=%s collections=%d items=%d target=%s warnings=%d",
        stac_object["id"],
        len(collection_uris),
        len(item_uris),
        catalog_uri,
        len(warnings),
    )
    return {
        "stac_object": stac_object,
        "warnings": warnings,
        "status": "completed",
        "exported": True,
        "catalog_uri": _full_uri(endpoint, catalog_uri),
        "collection_uris": [_full_uri(endpoint, uri) for uri in collection_uris],
        "item_uris": [_full_uri(endpoint, uri) for uri in item_uris],
    }


# ─── Sources ────────────────────────────────────────────────────────────────


def _read_collection_folder(
    folder: str,
    s3: S3ConnectionParameters | None,
    warnings: list[str],
) -> list[Scope]:
    """Read the collection trees under ``folder`` (local dir or s3:// prefix).

    Every ``*.json`` file is parsed and classified. Each Collection claims its
    containing subdirectory; Features belong to the nearest enclosing
    collection directory (paths relative to it). Invalid or misplaced files
    warn and are skipped; each collection must sit in its own directory, and
    collection directories must not nest.
    """

    entries = _list_json(folder, s3)
    parsed: list[tuple[str, dict[str, Any]]] = []
    for rel, raw in entries:
        if raw is None:
            warnings.append(f"{rel}: skipped unreadable JSON file.")
            continue
        parsed.append((rel, raw))

    collection_dirs: dict[str, dict[str, Any]] = {}
    for rel, data in parsed:
        if data.get("type") == "Collection":
            directory = posixpath.dirname(rel)
            if not directory:
                raise ValueError(
                    f"{rel}: collection sits at the top of source_folder; put each "
                    "collection in its own subdirectory (e.g. <id>/collection.json)."
                )
            existing = collection_dirs.get(directory)
            if existing is not None and existing.get("id") != data.get("id"):
                raise ValueError(f"{directory}: two different collections share this directory.")
            collection_dirs[directory] = data

    if not collection_dirs:
        raise ValueError(f"No STAC Collections found under source_folder: {folder}")

    directories = sorted(collection_dirs, key=lambda d: d.count("/"))
    for index, outer in enumerate(directories):
        for inner in directories[index + 1:]:
            if inner.startswith(outer + "/"):
                raise ValueError(
                    f"{inner}: nested collection directories are not supported "
                    f"({outer} contains another collection)."
                )

    claimed: set[str] = set()
    scopes: list[Scope] = []
    # Deeper directories claim their files first.
    for directory in sorted(collection_dirs, key=lambda d: d.count("/"), reverse=True):
        collection = collection_dirs[directory]
        items: list[tuple[str, dict[str, Any]]] = []
        for rel, data in parsed:
            if rel in claimed or not rel.startswith(directory + "/"):
                continue
            claimed.add(rel)
            if data.get("type") != "Feature":
                continue
            try:
                validate_item(data)
            except ValueError as exc:
                warnings.append(f"{rel}: skipped invalid STAC Item ({exc}).")
                continue
            items.append((rel.removeprefix(directory + "/"), data))
        if not items:
            warnings.append(f"{directory}/{_COLLECTION_FILENAME}: no items found under this collection.")
        scopes.append((collection, items))

    for rel, data in parsed:
        if rel not in claimed and data.get("type") == "Feature":
            warnings.append(f"{rel}: skipped, not inside any collection directory.")

    scopes.sort(key=lambda scope: str(scope[0].get("id", "")))
    return scopes


def _fetch_collection_links(links: list[str], warnings: list[str]) -> list[Scope]:
    """Fetch ``collection_links`` and their item files; unreadable parts warn and are skipped."""

    scopes: list[Scope] = []
    for link in links:
        try:
            document = fetch_json(link)
        except UnsafeUrlError as exc:
            warnings.append(f"collection link '{link}' skipped: {exc}")
            continue
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"collection link '{link}' skipped: {_exc_message(exc)}")
            continue
        if document.get("type") != "Collection":
            warnings.append(f"collection link '{link}' skipped: not a STAC Collection (type != 'Collection').")
            continue
        try:
            validate_collection(document)
        except ValueError as exc:
            warnings.append(f"collection link '{link}' skipped: invalid STAC Collection ({exc}).")
            continue
        items = _fetch_linked_items(document, link, warnings)
        if not items:
            warnings.append(
                f"collection link '{link}': no item files could be fetched via its 'item' links; "
                "the collection is used without items."
            )
        scopes.append((document, items))
    return scopes


def _fetch_linked_items(
    collection: dict[str, Any],
    collection_uri: str,
    warnings: list[str],
) -> list[tuple[str, dict[str, Any]]]:
    """Fetch the collection's items via its ``item`` links, resolved against the collection URI."""

    items: list[tuple[str, dict[str, Any]]] = []
    for href in _item_hrefs(collection):
        resolved = _resolve_href(collection_uri, href)
        if resolved is None:
            warnings.append(f"{collection_uri}: item link '{href}' skipped: cannot be resolved.")
            continue
        try:
            document = fetch_json(resolved)
        except UnsafeUrlError as exc:
            warnings.append(f"{collection_uri}: item link '{href}' skipped: {exc}")
            continue
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"{collection_uri}: item link '{href}' skipped: {_exc_message(exc)}")
            continue
        if document.get("type") != "Feature":
            warnings.append(f"{collection_uri}: item link '{href}' skipped: not a STAC Item (type != 'Feature').")
            continue
        try:
            validate_item(document)
        except ValueError as exc:
            warnings.append(f"{collection_uri}: item link '{href}' skipped: invalid STAC Item ({exc}).")
            continue
        item_rel = _href_relative_path(collection_uri, href)
        items.append((item_rel, document))
    return items


def _item_hrefs(collection: dict[str, Any]) -> list[str]:
    links = collection.get("links")
    if not isinstance(links, list):
        return []
    return [
        href
        for link in links
        if isinstance(link, dict) and link.get("rel") == "item" and isinstance(href := link.get("href"), str) and href
    ]


def _resolve_href(base_uri: str, href: str) -> str | None:
    """Resolve one item-link href against the collection's location; None when unresolvable."""

    lowered = href.lower()
    if lowered.startswith(("http://", "https://", "s3://")):
        return href
    if base_uri.lower().startswith(("http://", "https://")):
        return urljoin(base_uri, href)
    if base_uri.lower().startswith("s3://"):
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_uri), href))
    if urlparse(href).scheme:
        return None  # exotic scheme the guard would reject anyway
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_uri), href))


def _href_relative_path(collection_uri: str, href: str) -> str:
    """Tree-relative path for one fetched item: resolved path minus the collection's directory."""

    resolved = _resolve_href(collection_uri, href) or href
    path = posixpath.normpath(_uri_path(resolved))
    base = posixpath.dirname(_uri_path(collection_uri))
    if base and path.startswith(base + "/"):
        return path[len(base) + 1:]
    return posixpath.basename(path)


def _uri_path(uri: str) -> str:
    """Pure hierarchical path of a URI (no scheme/netloc)."""

    lowered = uri.lower()
    if lowered.startswith(("http://", "https://")):
        return urlparse(uri).path.lstrip("/")
    if lowered.startswith("s3://"):
        return uri[len("s3://"):]
    return uri


# ─── Export ─────────────────────────────────────────────────────────────────


def _export_catalog_tree(
    stac_object: dict[str, Any],
    scopes: list[Scope],
    target: str,
    *,
    warnings: list[str],
    overwrite: bool,
    s3: S3ConnectionParameters | None,
) -> tuple[dict[str, Any], str, list[str], list[str]]:
    """Copy each collection under ``<target>/<collection-id>/``, re-linked to the catalog.

    Standard self-contained layout: ``catalog.json`` at the target root and
    each collection in its own folder directly next to it (STAC best
    practice). Collection ids that are unsafe as folder names are slugified,
    with a warning. The catalog's child links are relative; every
    collection.json gains root/parent links to the catalog and fresh item
    links; every item copy gains root (catalog), parent/collection (its
    collection). Item assets and non-topological links are preserved as-is.
    """

    duplicate_ids = _duplicate_collection_ids(scopes)
    if duplicate_ids:
        raise ValueError(
            f"Duplicate collection id(s) across sources: {', '.join(sorted(duplicate_ids))} — "
            "each collection must appear once."
        )

    dirname_of = _collection_dirnames(scopes, warnings)

    catalog_rel = _CATALOG_FILENAME
    payloads: list[tuple[str, str]] = []
    collection_uris: list[str] = []
    item_uris: list[str] = []
    child_ids: list[str] = []

    for collection, items in scopes:
        collection_id = str(collection["id"])
        dest_dir = dirname_of[collection_id]
        collection_rel = f"{dest_dir}/{_COLLECTION_FILENAME}"
        up_to_catalog = posixpath.relpath(catalog_rel, dest_dir)

        kept_links = [
            dict(link)
            for link in collection.get("links") or []
            if isinstance(link, dict) and link.get("rel") not in {"root", "parent", "item", "self", "child"}
        ]
        safe_item_rels = [_safe_item_rel(item_rel, dest_dir, warnings) for item_rel, _ in items]
        collection_json = dict(collection)
        collection_json["links"] = [
            *kept_links,
            {"rel": "root", "href": up_to_catalog},
            {"rel": "parent", "href": up_to_catalog},
            *[{"rel": "item", "href": item_rel} for item_rel in safe_item_rels],
        ]
        validate_collection(collection_json)
        payloads.append((collection_rel, json.dumps(collection_json, indent=2)))
        collection_uris.append(f"{target}/{collection_rel}")
        child_ids.append(collection_id)

        for (item_rel, item), safe_rel in zip(items, safe_item_rels, strict=True):
            item_dest_rel = f"{dest_dir}/{safe_rel}"
            item_dir = posixpath.dirname(item_dest_rel)
            parent_href = posixpath.relpath(collection_rel, item_dir)
            kept_item_links = [
                dict(link)
                for link in item.get("links") or []
                if isinstance(link, dict) and link.get("rel") not in {"root", "parent", "collection", "self"}
            ]
            item_json = dict(item)
            item_json["collection"] = collection_id
            item_json["links"] = [
                *kept_item_links,
                {"rel": "root", "href": posixpath.relpath(catalog_rel, item_dir)},
                {"rel": "parent", "href": parent_href},
                {"rel": "collection", "href": parent_href},
            ]
            validate_item(item_json)
            payloads.append((item_dest_rel, json.dumps(item_json, indent=2)))
            item_uris.append(f"{target}/{item_dest_rel}")

    stac_object["links"] = [
        {"rel": "root", "href": catalog_rel},
        *[{"rel": "child", "href": f"{dirname_of[cid]}/{_COLLECTION_FILENAME}"} for cid in child_ids],
    ]
    validate_catalog(stac_object)
    payloads.insert(0, (catalog_rel, json.dumps(stac_object, indent=2)))

    written = _write_files(target, payloads, overwrite=overwrite, s3=s3)
    catalog_uri = written[0]
    logger.info("Exported catalog tree: %d file(s) under %s", len(written), target)
    return stac_object, catalog_uri, collection_uris, item_uris


def _collection_dirnames(scopes: list[Scope], warnings: list[str]) -> dict[str, str]:
    """Folder name per collection id: the id when folder-safe, else its slug.

    Raises when two collections would share one folder (e.g. ids that slugify
    to the same name).
    """

    taken: dict[str, str] = {}
    dirname_of: dict[str, str] = {}
    for collection, _ in scopes:
        collection_id = str(collection["id"])
        dirname = safe_export_name(collection_id, fallback="collection")
        if dirname != collection_id:
            warnings.append(
                f"collection id '{collection_id}' is not safe as a folder name; "
                f"its files are exported under '{dirname}/'."
            )
        previous = taken.get(dirname)
        if previous is not None:
            raise ValueError(
                f"Collections '{previous}' and '{collection_id}' both map to the folder "
                f"'{dirname}/' — give them distinct ids."
            )
        taken[dirname] = collection_id
        dirname_of[collection_id] = dirname
    return dirname_of


def _safe_item_rel(item_rel: str, dest_dir: str, warnings: list[str]) -> str:
    """Item copy path with every path segment made export-safe (warned when changed)."""

    safe_rel = "/".join(safe_export_name(segment, fallback="item") for segment in item_rel.split("/"))
    if safe_rel != item_rel:
        warnings.append(
            f"item path '{dest_dir}/{item_rel}' is not safe as an export path; "
            f"exported as '{dest_dir}/{safe_rel}'."
        )
    return safe_rel


def _duplicate_collection_ids(scopes: list[Scope]) -> set[str]:
    ids = [str(collection.get("id", "")) for collection, _ in scopes]
    return {value for value in ids if value and ids.count(value) > 1}


def _write_files(
    target: str,
    payloads: list[tuple[str, str]],
    *,
    overwrite: bool,
    s3: S3ConnectionParameters | None,
) -> list[str]:
    """Write (rel_path, json) pairs under the target; returns full URIs in order."""

    def _guarded(rel: str) -> bool:
        return rel == _CATALOG_FILENAME or rel.endswith(f"/{_COLLECTION_FILENAME}")

    if is_s3_uri(target):
        fs, root = s3_url_to_fs(target, storage_options=resolve_s3_storage_options(s3))
        root = root.rstrip("/")
        if not overwrite:
            for rel, _ in payloads:
                if _guarded(rel) and fs.exists(f"{root}/{rel}"):
                    raise FileExistsError(f"Target already exists: {target}/{rel}. Set overwrite=true to replace it.")
        for rel, payload in payloads:
            fs.write_text(f"{root}/{rel}", payload)
        return [f"{target}/{rel}" for rel, _ in payloads]

    base = Path(target)
    base.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        for rel, _ in payloads:
            if _guarded(rel) and (base / rel).exists():
                raise FileExistsError(f"Target already exists: {base / rel}. Set overwrite=true to replace it.")
    for rel, payload in payloads:
        destination = base / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    return [str(base / rel) for rel, _ in payloads]


# ─── Derivation ─────────────────────────────────────────────────────────────


def _resolve_target(target: str, *, local_output: str | None, bucket: str | None) -> str:
    """Full destination folder: s3://bucket/<target>, or a local folder path."""
    if local_output:
        return local_output
    if not bucket:
        raise ValueError(
            "bucket is required: the bearer token carries no username from which to "
            "derive the default oidc-<username> bucket."
        )
    return f"s3://{bucket}/{target.lstrip('/')}"


def _full_uri(endpoint: str, uri: str | None) -> str | None:
    """Full https URL for an s3:// URI (like the other features return); local paths pass through."""

    if uri is None or not is_s3_uri(uri):
        return uri
    return full_s3_url(endpoint, uri.removeprefix("s3://"))


def _s3_connection(request: SuggestCatalogRequest) -> S3ConnectionParameters | None:
    """Explicit connection parameters (already all-or-nothing via the model), else None."""

    if not request.endpoint_url:
        return None
    return S3ConnectionParameters(
        endpoint_url=request.endpoint_url,
        access_key_id=request.access_key_id,  # type: ignore[arg-type]  # model enforces all-or-nothing
        secret_access_key=request.secret_access_key,  # type: ignore[arg-type]
        session_token=request.session_token,
    )


def _derive_catalog(
    request: SuggestCatalogRequest,
    collections: list[dict[str, Any]],
    default_username: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Derive the Catalog JSON: identity + metadata, enriched from the collections.

    Catalogs carry no extent; what can be lifted from the children is the
    keyword vocabulary (union) and the providers (merged, deduplicated).
    """

    base = request.base
    slug = _slugify(request.title or (base.get("title") if base else None) or "catalog")

    catalog_id = _propose_id(request.id, base, slug, default_username, warnings)

    stac_object: dict[str, Any] = {
        "type": "Catalog",
        "stac_version": "1.1.0",
        "id": catalog_id,
        "description": (
            request.description
            or (base.get("description") if base else None)
            or request.title
            or catalog_id
        ),
        # Links are wired at export time, when the child hrefs exist.
        "links": [],
    }
    if request.title or (base and base.get("title")):
        stac_object["title"] = request.title or (base.get("title") if base else None)
    if base and base.get("stac_extensions"):
        stac_object["stac_extensions"] = base["stac_extensions"]

    keywords = _union_keywords(collections, base)
    if keywords:
        stac_object["keywords"] = keywords
        warnings.append(f"keywords unioned from {len(collections)} collection(s): {', '.join(keywords)}")

    providers = _union_providers(collections, base)
    if providers:
        stac_object["providers"] = providers
        warnings.append(f"providers merged from the collections: {', '.join(str(p.get('name')) for p in providers)}")

    if collections:
        child_ids = sorted({str(collection["id"]) for collection in collections if collection.get("id")})
        warnings.append(f"{len(child_ids)} child collection(s) noted: {', '.join(child_ids)}")
    if not request.description and not (base and base.get("description")):
        warnings.append("description fell back to title/id: set an explicit description in the builder form")

    validate_catalog(stac_object)
    return stac_object


def _union_keywords(collections: list[dict[str, Any]], base: dict[str, Any] | None) -> list[str]:
    keywords: list[str] = []
    if base and isinstance(base.get("keywords"), list):
        keywords.extend(str(k) for k in base["keywords"] if str(k).strip())
    for collection in collections:
        value = collection.get("keywords")
        if isinstance(value, list):
            keywords.extend(str(k) for k in value if str(k).strip())
    return list(dict.fromkeys(keywords))


def _union_providers(collections: list[dict[str, Any]], base: dict[str, Any] | None) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    if base and isinstance(base.get("providers"), list):
        providers.extend(dict(p) for p in base["providers"] if isinstance(p, dict))
    for collection in collections:
        value = collection.get("providers")
        if not isinstance(value, list):
            continue
        for provider in value:
            if not isinstance(provider, dict):
                continue
            key = json.dumps({"name": provider.get("name"), "url": provider.get("url")}, sort_keys=True)
            if key not in {json.dumps({"name": p.get("name"), "url": p.get("url")}, sort_keys=True) for p in providers}:
                providers.append(dict(provider))
    return providers


def _list_json(folder: str, s3: S3ConnectionParameters | None) -> list[tuple[str, dict[str, Any] | None]]:
    """Parse every ``*.json`` file under the folder; unreadable files come back as None."""

    if is_s3_uri(folder):
        fs, root = s3_url_to_fs(folder, storage_options=resolve_s3_storage_options(s3))
        root = root.rstrip("/")
        entries = sorted(str(path) for path in fs.find(root, withdirs=False) if str(path).lower().endswith(".json"))
        return [
            (PurePosixPath(path).relative_to(root).as_posix(), _read_json_bytes(_read_bytes(fs, path, remote=True)))
            for path in entries
        ]
    base = Path(folder)
    if not base.is_dir():
        raise FileNotFoundError(f"source_folder not found or not a directory: {folder}")
    return [(path.relative_to(base).as_posix(), _read_json_bytes(_read_bytes(None, str(path), remote=False))) for path in sorted(base.rglob("*.json"))]


def _read_bytes(fs: Any, path: str, *, remote: bool) -> bytes | None:
    try:
        if remote:
            with fs.open(path, "rb") as handle:
                return handle.read()
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning("Skipped unreadable file %s: %s", path, exc)
        return None


def _read_json_bytes(payload: bytes | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Skipped unparseable JSON: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _exc_message(exc: Exception) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _slugify(value: str) -> str:
    return slugify(value, "catalog")


def _propose_id(
    explicit: str | None,
    base: dict[str, Any] | None,
    slug: str,
    default_username: str | None,
    warnings: list[str],
) -> str:
    if explicit:
        return explicit
    if base:
        # Ids are flat: keep only the base id's last segment and join with '-'.
        base_segment = str(base["id"]).rstrip("/").rsplit("/", 1)[-1]
        proposed = f"{base_segment}-{slug}"
        warnings.append(f"id derived from the base id and title slug: '{proposed}'")
        return proposed
    if not default_username:
        raise ValueError(
            "Cannot propose a catalog id: no explicit id was given and no username could "
            "be derived from your bearer token. Pass an explicit id."
        )
    proposed = f"{default_username}-{slug}"
    warnings.append(
        f"id proposed from your username and title slug: '{proposed}' — it must be unique "
        "among the project catalogs you own (GET /v1/edito/stac/mycatalogs); EDITO adds its "
        "infrastructure root at publish time"
    )
    return proposed
