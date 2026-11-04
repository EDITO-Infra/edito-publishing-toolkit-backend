"""Use-case logic for the STAC catalog tree builder.

Creates one full catalog tree — the "create catalog from collections" and
"create full catalog from catalogs/collections" capabilities in one route; the
inputs determine the shape.

- **Preview** — inline ``collections``/``catalogs``, fetched
  ``collection_links``/``catalog_links``, and/or a ``base``: pure metadata.
  Catalogs carry no extent; the derived catalog is identity + metadata,
  enriched from the sources (keywords unioned, providers merged). Child links
  are wired at export time because the child hrefs don't exist yet.
- **Export** — every source contributes its collection children, which become
  direct children of the new catalog: collections copied under
  ``<target>/<id>/`` with their item files fetched via their ``item`` links;
  each catalog source's collection children pulled up the same way (same-id
  collections combined once — the source catalog object itself is not copied,
  and catalogs below that level are skipped with a warning).
  ``source_prefix`` (subfolders of self-contained collection
  trees, the layout the collection tree route exports) is read the same way.
  The written tree is complete, publishable, and self-contained — every
  object schema-validated, with a per-object report in the response.
"""

from __future__ import annotations

import json
import logging
import posixpath
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ept.infrastructure.formats.stac import StacObjectType, get_stac_object_type
from ept.infrastructure.formats.stac.catalog import (
    CollectionScope,
    catalog_tree_payloads,
    validate_catalog,
)
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
    slugify,
)
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import CreateCatalogTreeRequest

logger = logging.getLogger(__name__)

_CATALOG_FILENAME = "catalog.json"
_COLLECTION_FILENAME = "collection.json"

# One gathered catalog source: (catalog dict, its collection scopes).
CatalogScope = tuple[dict[str, Any], list[CollectionScope]]


# ─── Orchestration ──────────────────────────────────────────────────────────


def create_catalog_tree(
    request: CreateCatalogTreeRequest,
    default_username: str | None = None,
    default_bucket: str | None = None,
) -> dict[str, Any]:
    """Create a STAC Catalog tree from collections and/or catalogs; optionally export it."""

    warnings: list[str] = []
    s3 = _s3_connection(request)

    destination = request.target or request.local_output
    export = request.source_prefix is not None or (
        bool(request.collection_links or request.catalog_links) and destination is not None
    )
    target: str | None = None
    if export:
        if destination is None:  # unreachable via the model validators (source_prefix requires a target)
            raise ValueError("source_prefix always exports: provide `target` or `local_output`.")
        target = _resolve_target(
            destination,
            local_output=request.local_output,
            bucket=request.bucket or default_bucket,
        )

    # Step 1 — gather the sources.
    collection_scopes: list[CollectionScope] = []
    if request.source_prefix is not None:
        collection_scopes.extend(_read_collection_prefix(request.source_prefix, s3, warnings))
        logger.info("Step 1: %d collection tree(s) read from source_prefix %s", len(collection_scopes), request.source_prefix)
    if request.collection_links:
        collection_scopes.extend(_fetch_collection_links(request.collection_links, warnings))

    catalog_scopes: list[CatalogScope] = []
    for catalog in request.catalogs:
        catalog_scopes.append(_catalog_child_scopes(catalog, "inline catalog", warnings))
    if request.catalog_links:
        catalog_scopes.extend(_fetch_catalog_scopes(request.catalog_links, warnings))
    if request.catalogs or request.catalog_links:
        logger.info("Step 1: %d catalog source(s) gathered", len(catalog_scopes))

    # Step 2 — combine the sources: every catalog source contributes its
    # collection children; all collections become direct children of the new
    # catalog (same-id duplicates are combined once).
    combined = _combine_sources(collection_scopes, catalog_scopes, warnings)
    logger.info("Step 2: %d collection(s) combined from the sources", len(combined))

    # Derive the catalog metadata from the combined sources.
    # Without a base, the first catalog source seeds title/description/keywords/providers.
    base = request.base
    if base is None and catalog_scopes:
        base = catalog_scopes[0][0]
        warnings.append(
            f"catalog source '{base.get('id')}' seeds the new catalog's metadata; "
            "pass an explicit base to override"
        )
    stac_object = _derive_catalog(
        request,
        [*request.collections, *(collection for collection, _ in combined)],
        base,
        default_username,
        warnings,
    )
    logger.info("Step 2: catalog '%s' derived", stac_object["id"])

    if not export:
        logger.info(
            "Created STAC Catalog id=%s collections=%d catalogs=%d base=%s exported=false warnings=%d",
            stac_object["id"],
            len(combined),
            len(catalog_scopes),
            bool(request.base),
            len(warnings),
        )
        return {
            "stac_object": stac_object,
            "warnings": warnings,
            "status": "preview",
            "exported": False,
            "catalog_uri": None,
            "collection_uris": [],
            "item_uris": [],
            "objects": [],
        }

    # Step 3+4 — build the tree payloads and write them.
    payloads = catalog_tree_payloads(
        stac_object,
        combined,
        warnings=warnings,
    )
    written = _write_files(target, payloads, overwrite=request.overwrite, s3=s3)  # type: ignore[arg-type]  # export implies target
    catalog_uri = written[0]
    collection_uris = [
        uri for (rel, _), uri in zip(payloads, written, strict=True) if rel.endswith(f"/{_COLLECTION_FILENAME}")
    ]
    item_uris = [
        uri
        for (rel, _), uri in zip(payloads, written, strict=True)
        if rel != _CATALOG_FILENAME and not rel.endswith(f"/{_COLLECTION_FILENAME}")
    ]
    endpoint = s3_endpoint_url(s3) or ""
    logger.info(
        "Created STAC Catalog tree id=%s collections=%d catalog_sources=%d items=%d target=%s warnings=%d",
        stac_object["id"],
        len(collection_uris),
        len(catalog_scopes),
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
        "objects": _object_reports(payloads),
    }


def _combine_sources(
    collection_scopes: list[CollectionScope],
    catalog_scopes: list[CatalogScope],
    warnings: list[str],
) -> list[CollectionScope]:
    """Flatten every source's collections into one child list, combined by id.

    The same collection reached through multiple sources (e.g. passed directly
    and inside a catalog source) is combined once; the same id with different
    content is ambiguous and rejected.
    """

    combined: list[CollectionScope] = []
    by_id: dict[str, dict[str, Any]] = {}
    flattened = [*collection_scopes]
    for _, scopes in catalog_scopes:
        flattened.extend(scopes)
    for collection, items in flattened:
        collection_id = str(collection["id"])
        previous = by_id.get(collection_id)
        if previous is None:
            by_id[collection_id] = collection
            combined.append((collection, items))
        elif previous != collection:
            raise ValueError(
                f"Collection id '{collection_id}' appears in multiple sources with "
                "different content — pass it once (the sources are combined by id)."
            )
        else:
            warnings.append(
                f"collection '{collection_id}' appears in multiple sources; combined once."
            )
    return combined


# ─── Sources ────────────────────────────────────────────────────────────────


def _read_collection_prefix(
    folder: str,
    s3: S3ConnectionParameters | None,
    warnings: list[str],
) -> list[CollectionScope]:
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
                    f"{rel}: collection sits at the top of source_prefix; put each "
                    "collection in its own subdirectory (e.g. <id>/collection.json)."
                )
            existing = collection_dirs.get(directory)
            if existing is not None and existing.get("id") != data.get("id"):
                raise ValueError(f"{directory}: two different collections share this directory.")
            collection_dirs[directory] = data

    if not collection_dirs:
        raise ValueError(f"No STAC Collections found under source_prefix: {folder}")

    directories = sorted(collection_dirs, key=lambda d: d.count("/"))
    for index, outer in enumerate(directories):
        for inner in directories[index + 1:]:
            if inner.startswith(outer + "/"):
                raise ValueError(
                    f"{inner}: nested collection directories are not supported "
                    f"({outer} contains another collection)."
                )

    claimed: set[str] = set()
    scopes: list[CollectionScope] = []
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


def _fetch_collection_links(links: list[str], warnings: list[str]) -> list[CollectionScope]:
    """Fetch ``collection_links`` and their item files; unreadable parts warn and are skipped."""

    scopes: list[CollectionScope] = []
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


def _fetch_catalog_scopes(links: list[str], warnings: list[str]) -> list[CatalogScope]:
    """Fetch ``catalog_links`` and their collection children; unreadable parts warn and are skipped."""

    scopes: list[CatalogScope] = []
    for link in links:
        try:
            document = fetch_json(link)
        except UnsafeUrlError as exc:
            warnings.append(f"catalog link '{link}' skipped: {exc}")
            continue
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"catalog link '{link}' skipped: {_exc_message(exc)}")
            continue
        if document.get("type") != "Catalog":
            warnings.append(f"catalog link '{link}' skipped: not a STAC Catalog (type != 'Catalog').")
            continue
        try:
            validate_catalog(document)
        except ValueError as exc:
            warnings.append(f"catalog link '{link}' skipped: invalid STAC Catalog ({exc}).")
            continue
        scopes.append(_catalog_child_scopes(document, link, warnings))
    return scopes


def _catalog_child_scopes(catalog: dict[str, Any], catalog_uri: str, warnings: list[str]) -> CatalogScope:
    """Fetch the catalog's collection children (one level) and their item files.

    The children are combined into the new catalog's direct children by the
    caller; the source catalog object itself is not copied. Catalog children
    of a catalog source cannot be flattened into collections and are skipped
    with a warning.
    """

    scopes: list[CollectionScope] = []
    for href in _child_hrefs(catalog):
        resolved = _resolve_href(catalog_uri, href)
        if resolved is None:
            warnings.append(f"{catalog_uri}: child link '{href}' skipped: cannot be resolved.")
            continue
        try:
            document = fetch_json(resolved)
        except UnsafeUrlError as exc:
            warnings.append(f"{catalog_uri}: child link '{href}' skipped: {exc}")
            continue
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"{catalog_uri}: child link '{href}' skipped: {_exc_message(exc)}")
            continue
        if document.get("type") == "Catalog":
            warnings.append(
                f"{catalog_uri}: child link '{href}' points at a nested catalog; "
                "skipped (catalog sources contribute their collection children; "
                "pass that catalog as its own source to combine it)."
            )
            continue
        if document.get("type") != "Collection":
            warnings.append(f"{catalog_uri}: child link '{href}' skipped: not a STAC Collection.")
            continue
        try:
            validate_collection(document)
        except ValueError as exc:
            warnings.append(f"{catalog_uri}: child link '{href}' skipped: invalid STAC Collection ({exc}).")
            continue
        items = _fetch_linked_items(document, resolved, warnings)
        if not items:
            warnings.append(
                f"{catalog_uri}: collection child '{href}': no item files could be fetched "
                "via its 'item' links; the collection is used without items."
            )
        scopes.append((document, items))
    return catalog, scopes


def _child_hrefs(catalog: dict[str, Any]) -> list[str]:
    """Deduplicated ``collection``/``child`` link hrefs of a catalog, in order."""

    links = catalog.get("links")
    if not isinstance(links, list):
        return []
    hrefs = [
        href
        for link in links
        if isinstance(link, dict)
        and link.get("rel") in ("collection", "child")
        and isinstance(href := link.get("href"), str)
        and href
    ]
    return list(dict.fromkeys(hrefs))


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
    """Resolve one link href against the parent object's location; None when unresolvable."""

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


def _write_files(
    target: str,
    payloads: list[tuple[str, str]],
    *,
    overwrite: bool,
    s3: S3ConnectionParameters | None,
) -> list[str]:
    """Write (rel_path, json) pairs under the target; returns full URIs in order."""

    def _guarded(rel: str) -> bool:
        return (
            rel == _CATALOG_FILENAME
            or rel.endswith(f"/{_CATALOG_FILENAME}")
            or rel.endswith(f"/{_COLLECTION_FILENAME}")
        )

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


def _object_reports(payloads: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Per-object validation report over the written payloads."""

    reports: list[dict[str, Any]] = []
    for rel, payload in payloads:
        data = json.loads(payload)
        kind = get_stac_object_type(data)
        issues: list[str]
        if kind is StacObjectType.UNKNOWN:
            issues = ["not a STAC object (no usable 'type')."]
        else:
            try:
                match kind:
                    case StacObjectType.CATALOG:
                        validate_catalog(data)
                    case StacObjectType.COLLECTION:
                        validate_collection(data)
                    case StacObjectType.ITEM:
                        validate_item(data)
                issues = []
            except ValueError as exc:
                issues = [str(exc)]
            except Exception as exc:  # stac-validator crashes on malformed objects
                issues = [f"STAC schema validation crashed: {exc}"]
        reports.append(
            {"path": rel, "object_type": kind, "object_id": data.get("id"), "validation_issues": issues}
        )
    return reports


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


def _s3_connection(request: CreateCatalogTreeRequest) -> S3ConnectionParameters | None:
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
    request: CreateCatalogTreeRequest,
    collections: list[dict[str, Any]],
    base: dict[str, Any] | None,
    default_username: str | None,
    warnings: list[str],
) -> dict[str, Any]:
    """Derive the Catalog JSON: identity + metadata, enriched from the sources.

    Catalogs carry no extent; what can be lifted from the children is the
    keyword vocabulary (union) and the providers (merged, deduplicated).
    """

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
        raise FileNotFoundError(f"source_prefix not found or not a directory: {folder}")
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
