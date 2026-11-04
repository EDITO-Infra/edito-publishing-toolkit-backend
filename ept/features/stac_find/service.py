"""STAC find use cases: simple scans of the EDITO STAC API.

These are EPT's own find/scan primitives, deliberately *not* named "search":
STAC API's official ``GET|POST /search`` is Item Search, and these scans are
not conformant to it. Six find primitives:

- ``find_item``       — STAC Item Search (``GET /search``) with optional free-text filter
- ``find_item_scientific`` — find items by Scientific extension metadata (``sci:doi``,
  ``sci:citation``, ``sci:publications``); server-side CQL2 filter with client-side fallback
- ``find_collection`` — list ``/collections`` (following ``next`` pages), free-text filter;
  the returned collection ``id`` values are what ``find_item``, ``find_asset`` and
  ``find_properties`` accept as their ``collections`` argument
- ``find_catalog``    — walk the catalog tree from the projects root, following ``rel="child"`` links recursively
- ``find_asset``      — find *items* by asset: items with an asset whose key/href/title
  matches ``q`` (a DOI, filename, …), searched inside ``collections``
- ``find_properties`` — find *items* by properties: items whose properties contain all
  ``key=value`` fragments (values substring-matched), searched inside ``collections``

All take an :class:`EditoStacApiClient` and the caller's access token; upstream
errors surface as the client's Problem Details exceptions.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ept.features.stac_find.models import (
    FindCatalogsResponse,
    FindCollectionsResponse,
    FindItemsResponse,
    FindPropertiesResponse,
)
from ept.infrastructure.services.editostacapi import (
    EditoStacApiClient,
    EditoStacApiRequestError,
    resolve_edito_stac_api_url,
)

__all__ = [
    "find_asset",
    "find_catalog",
    "find_collection",
    "find_item",
    "find_item_scientific",
    "find_properties",
]

_PROJECTS_ROOT = "/catalogs/projects"

# honey: hard cap on /collections pagination; raise if the API grows past this
_MAX_PAGES = 50

# honey: hard cap on catalog-tree depth; EDITO project catalogs nest shallowly
# today — raise this if deeper child trees appear upstream
_MAX_CATALOG_DEPTH = 5


async def find_item(
    api: EditoStacApiClient,
    *,
    access_token: str,
    bbox: list[float] | None = None,
    datetime: str | None = None,
    collections: list[str] | None = None,
    ids: list[str] | None = None,
    limit: int = 10,
    q: str | None = None,
) -> FindItemsResponse:
    """STAC Item Search via ``GET /search``; ``q`` narrows results client-side.

    ``collections`` takes collection ids — use :func:`find_collection` to
    discover them. If the exact-id search returns nothing — or upstream 404s
    on unknown ids — the ids are fuzzy-resolved against the collection
    catalog and the search is retried inside the resolved collections.
    """
    params: dict[str, str] = {"limit": str(limit)}
    if bbox:
        params["bbox"] = ",".join(str(v) for v in bbox)
    if datetime:
        params["datetime"] = datetime
    if collections:
        params["collections"] = ",".join(collections)
    if ids:
        params["ids"] = ",".join(ids)
    payload, features = await _search_items_or_404(api, access_token, params, q, collections)
    if collections and (not features or payload is None):
        resolved = await _resolve_collections(api, access_token, collections)
        if resolved and sorted(resolved) != sorted(collections):
            params["collections"] = ",".join(resolved)
            payload, features = await _search_items(api, access_token, params, q)
        elif payload is None:
            raise _no_match_404
    payload = payload or {}
    return FindItemsResponse(
        type=payload.get("type", "FeatureCollection"),
        features=features,
        # honey: number_matched is upstream's unfiltered count — approximate
        # when q filters client-side; number_returned must match features
        number_matched=payload.get("numberMatched"),
        number_returned=len(features),
    )


async def find_item_scientific(
    api: EditoStacApiClient,
    *,
    access_token: str,
    doi: str | None = None,
    citation: str | None = None,
    collections: list[str] | None = None,
    limit: int = 100,
) -> FindItemsResponse:
    """Find items carrying STAC Scientific extension metadata.

    ``doi`` matches ``sci:doi`` and ``sci:publications[].doi`` (case-insensitive
    equality); ``citation`` matches ``sci:citation`` and publication citations
    (case-insensitive substring). At least one of the two is required.

    The EDITO STAC API (resto) declares the STAC Filter Extension (CQL2) but
    restricts filters to its indexed queryables, which do not include the
    scientific properties. A server-side CQL2 filter is therefore attempted
    first; if upstream rejects it (400) or the search 404s on unknown
    collections, the search falls back to a client-side scan via
    :func:`find_item` (which includes the exact-id → fuzzy collection
    resolution).
    """
    if not doi and not citation:
        raise ValueError("Either 'doi' or 'citation' must be provided.")

    params: dict[str, str] = {"limit": str(limit), "filter-lang": "cql2-text"}
    if collections:
        params["collections"] = ",".join(collections)
    params["filter"] = _scientific_cql2_filter(doi, citation)

    try:
        payload = await api.get_json(_with_query("search", params), access_token=access_token)
        features = [f for f in payload.get("features", []) if isinstance(f, dict)]
    except EditoStacApiRequestError:
        # honey: upstream rejects scientific properties in CQL2 filters or 404s
        # on unknown collections — retry as client-side scan; revisit if EDITO
        # adds scientific fields to its queryables
        payload = None
        found = await find_item(
            api, access_token=access_token, collections=collections, limit=limit
        )
        features = [
            f for f in found.features if _matches_scientific(f, doi=doi, citation=citation)
        ]
        return FindItemsResponse(
            type=found.type,
            features=features,
            number_matched=found.number_matched,
            number_returned=len(features),
        )

    return FindItemsResponse(
        type=payload.get("type", "FeatureCollection"),
        features=features,
        number_matched=payload.get("numberMatched"),
        number_returned=len(features),
    )


async def find_collection(
    api: EditoStacApiClient,
    *,
    access_token: str,
    q: str | None = None,
    limit: int = 100,
) -> FindCollectionsResponse:
    """List collections (all ``next`` pages), narrowed by free-text ``q``.

    Call this first to obtain collection ``id`` values, then pass them as the
    ``collections`` argument of :func:`find_item`, :func:`find_asset` or
    :func:`find_properties`.
    """
    path: str | None = _with_query("collections", {"limit": str(limit)})
    collections: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGES):
        if path is None:
            break
        payload = await api.get_json(path, access_token=access_token)
        collections.extend(c for c in payload.get("collections", []) if isinstance(c, dict))
        path = _relative(_next_url(payload))
    if q:
        collections = [c for c in collections if _matches(q, _collection_text(c))]
    return FindCollectionsResponse(collections=collections)


async def find_catalog(
    api: EditoStacApiClient,
    *,
    access_token: str,
    q: str | None = None,
) -> FindCatalogsResponse:
    """Walk the catalog tree breadth-first from the projects root.

    Starts at ``/catalogs/projects`` and follows every ``rel="child"`` link,
    fetching each child catalog so nested children are included too. Each
    breadth-first level is fetched concurrently (one round trip per level);
    traversal is deduplicated on child href and capped at
    :data:`_MAX_CATALOG_DEPTH` levels. ``q`` filters the collected child
    links on title/href.
    """
    catalogs: list[dict[str, Any]] = []
    seen: set[str] = set()
    frontier = [_PROJECTS_ROOT]
    for _ in range(_MAX_CATALOG_DEPTH):
        payloads = await asyncio.gather(
            *(api.get_json(path, access_token=access_token) for path in frontier)
        )
        following: list[str] = []
        for payload in payloads:
            for link in payload.get("links", []):
                if not (
                    isinstance(link, dict)
                    and link.get("rel") == "child"
                    and link.get("href")
                ):
                    continue
                child = _relative(str(link["href"]))
                if child is None or child in seen:
                    continue
                seen.add(child)
                catalogs.append(link)
                following.append(child)
        if not following:
            break
        frontier = following
    if q:
        catalogs = [c for c in catalogs if _matches(q, str(c.get("title", "")), str(c.get("href", "")))]
    return FindCatalogsResponse(catalogs=catalogs)


async def find_asset(
    api: EditoStacApiClient,
    *,
    access_token: str,
    q: str,
    collections: list[str] | None = None,
    limit: int = 100,
) -> FindItemsResponse:
    """Find items by asset: ``q`` may be a DOI, filename, asset key or title.

    Each ``collections`` entry is first resolved against the collection
    catalog (via :func:`find_collection`), so a name fragment or DOI-bearing
    title works as well as an exact id. All pages of the collection's items
    are scanned (upstream does not index asset hrefs, so ``q`` cannot be
    pushed server-side) for assets whose key/href/title matches ``q``.
    Returns a FeatureCollection of the matching items (links intact); each
    item keeps only its matched assets.
    """
    resolved = await _resolve_collections(api, access_token, collections)
    params: dict[str, str] = {"limit": str(limit)}
    if resolved:
        params["collections"] = ",".join(resolved)
    number_matched, candidates = await _search_all_items(api, access_token, params)
    features: list[dict[str, Any]] = []
    for feature in candidates:
        assets = feature.get("assets") or {}
        matched = {
            key: asset
            for key, asset in assets.items()
            if _matches(q, key, str(asset.get("href", "")), str(asset.get("title", "")))
        }
        if matched:
            features.append({**feature, "assets": matched})
    # number_matched stays None: upstream's count is the unfiltered scan size,
    # not the number of asset matches — reporting it only confuses
    return FindItemsResponse(
        type="FeatureCollection",
        features=features,
        number_returned=len(features),
    )


async def find_properties(
    api: EditoStacApiClient,
    *,
    access_token: str,
    match: dict[str, str],
    collections: list[str] | None = None,
    limit: int = 100,
) -> FindPropertiesResponse:
    """Find items by properties: items whose properties contain every ``match`` pair.

    Keys compare exactly; values compare as case-insensitive substrings, so
    ``{"platform": "sentinel-1"}`` matches ``"Sentinel-1A"`` and citation or
    DOI fragments work too — ``{"sci:doi": "10.14279"}``,
    ``{"sci:citation": "Brexit"}``.
    """
    resolved = await _resolve_collections(api, access_token, collections)
    params: dict[str, str] = {"limit": str(limit)}
    if resolved:
        params["collections"] = ",".join(resolved)
    _scanned, candidates = await _search_all_items(api, access_token, params)
    lowered = {k.lower(): v.lower() for k, v in match.items()}
    features = [
        f
        for f in candidates
        if all(
            value in str((f.get("properties") or {}).get(key, "")).lower()
            for key, value in lowered.items()
        )
    ]
    return FindPropertiesResponse(features=features)


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _search_all_items(
    api: EditoStacApiClient,
    access_token: str,
    params: dict[str, str],
) -> tuple[int | None, list[dict[str, Any]]]:
    """Every item of ``GET /search?params``: follows ``next`` pages, capped.

    Returns the first page's ``numberMatched`` and all collected features.
    """
    path: str | None = _with_query("search", params)
    number_matched: int | None = None
    features: list[dict[str, Any]] = []
    for _ in range(_MAX_PAGES):
        if path is None:
            break
        payload = await api.get_json(path, access_token=access_token)
        if number_matched is None:
            number_matched = payload.get("numberMatched")
        features.extend(f for f in payload.get("features", []) if isinstance(f, dict))
        path = _relative(_next_url(payload))
    return number_matched, features


async def _search_items(
    api: EditoStacApiClient,
    access_token: str,
    params: dict[str, str],
    q: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One ``GET /search`` round trip; returns the raw payload and ``q``-filtered features."""
    payload = await api.get_json(_with_query("search", params), access_token=access_token)
    features = [f for f in payload.get("features", []) if _matches(q, _feature_text(f))]
    return payload, features


_no_match_404 = EditoStacApiRequestError(
    404, {"detail": "No matching collections for the requested search."}
)


async def _search_items_or_404(
    api: EditoStacApiClient,
    access_token: str,
    params: dict[str, str],
    q: str | None,
    collections: list[str] | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """First search pass; upstream 404s are deferred so collection resolution can retry.

    Returns ``payload=None`` when upstream 404'd (unknown collection ids 404
    rather than returning an empty FeatureCollection); other errors raise as
    usual.
    """
    try:
        return await _search_items(api, access_token, params, q)
    except EditoStacApiRequestError as exc:
        if exc.status_code == 404 and collections:
            return None, []
        raise


async def _resolve_collections(
    api: EditoStacApiClient,
    access_token: str,
    collections: list[str] | None,
) -> list[str] | None:
    """Resolve collection name fragments to real collection ids.

    Each entry is matched against the collection catalog (id/title/description/
    keywords via :func:`find_collection`); entries matching nothing are kept
    as-is, so exact ids still work and upstream search surfaces the error.
    """
    # honey: one full /collections pagination per entry; fine for a handful of
    # hints — batch/deduplicate the fetch if callers pass many
    if not collections:
        return None
    resolved: list[str] = []
    for hint in collections:
        matched = await find_collection(api, access_token=access_token, q=hint)
        resolved.extend(str(c["id"]) for c in matched.collections if c.get("id"))
        if not matched.collections:
            resolved.append(hint)
    return list(dict.fromkeys(resolved))


def _scientific_cql2_filter(doi: str | None, citation: str | None) -> str:
    """Build a CQL2-text filter on scientific extension properties."""
    conditions: list[str] = []
    if doi:
        escaped = doi.replace("'", "''")
        conditions.append(f'"sci:doi" = \'{escaped}\'')
    if citation:
        escaped = citation.replace("'", "''")
        conditions.append(f'"sci:citation" LIKE \'%{escaped}%\'')
    return " AND ".join(f"({c})" for c in conditions)


def _matches_scientific(
    feature: dict[str, Any], *, doi: str | None, citation: str | None
) -> bool:
    """Client-side match on ``sci:doi``/``sci:publications[].doi`` and
    ``sci:citation``/publication citations."""
    properties = feature.get("properties") or {}
    publications = properties.get("sci:publications")
    publications = publications if isinstance(publications, list) else []
    pub_values = [
        str(p.get(key, ""))
        for p in publications
        if isinstance(p, dict)
        for key in (["doi"] if doi else ["citation"])
    ]
    if doi is not None:
        needle = doi.strip().lower()
        candidates = [str(properties.get("sci:doi", "")), *pub_values]
        if not any(needle == c.strip().lower() for c in candidates if c):
            return False
    if citation is not None:
        needle = citation.strip().lower()
        pub_citations = [
            str(p.get("citation", ""))
            for p in publications
            if isinstance(p, dict)
        ]
        candidates = [str(properties.get("sci:citation", "")), *pub_citations]
        if not any(needle in c.lower() for c in candidates if c):
            return False
    return True


def _with_query(path: str, params: dict[str, str]) -> str:
    from urllib.parse import urlencode

    return f"{path}?{urlencode(params)}" if params else path


def _next_url(payload: dict[str, Any]) -> str | None:
    for link in payload.get("links", []):
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
            return str(link["href"])
    return None


def _relative(url: str | None) -> str | None:
    """Reduce an absolute ``next`` href to a path the API client can GET."""
    if url is None:
        return None
    base = resolve_edito_stac_api_url()
    return url[len(base):].lstrip("/") if url.startswith(base) else url


def _matches(q: str | None, *values: str) -> bool:
    if not q:
        return True
    needle = q.lower()
    return any(needle in value.lower() for value in values if value)


def _feature_text(feature: dict[str, Any]) -> str:
    properties = feature.get("properties") or {}
    return " ".join(
        [
            str(feature.get("id", "")),
            str(properties.get("title", "")),
            str(properties.get("description", "")),
            str(properties.get("sci:doi", "")),
            str(properties.get("sci:citation", "")),
        ]
    )


def _collection_text(collection: dict[str, Any]) -> str:
    return " ".join(
        [
            str(collection.get("id", "")),
            str(collection.get("title", "")),
            str(collection.get("description", "")),
            " ".join(str(k) for k in collection.get("keywords", [])),
        ]
    )
