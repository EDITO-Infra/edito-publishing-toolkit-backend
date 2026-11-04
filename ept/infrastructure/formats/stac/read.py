"""STAC format primitives: throttling-aware reads, object typing, tree
traversal, API search.

Thin, async, transport-level helpers only — every primitive takes the caller's
``httpx.AsyncClient`` (routes obtain it from ``HttpClientDep`` in
``ept.api.dependencies``, so tests can override it). No validation logic and
no EDITO policy live here: features compose these primitives and own all
policy.

Objects are handled as raw JSON dicts end to end — no pystac parsing. This
avoids pystac's re-serialization pitfalls (``preserve_dict``, ``transform_hrefs``,
root-link re-resolution) entirely: the dict fetched over the wire is the dict
handed to consumers. Bearer-token auth rides on every GET via headers.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import time
from typing import Any, Optional
from urllib.parse import quote, urljoin

import httpx

import logging

from ept.infrastructure.utils.urlguard import (
    UnsafeUrlError,
    public_http_url,
    same_http_host,
)

logger = logging.getLogger(__name__)

# Per-request HTTP timeout default for the primitives (callers pass their own).
_DEFAULT_TIMEOUT_SECONDS = 30

# Statuses worth retrying: throttling plus transient server-side failures.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_FETCH_ATTEMPTS = 4
# The root object gates the entire traversal — aborting on it loses the whole
# run. Servers that answer bursts with bare 500s (no Retry-After) can stay
# degraded past the default 1+2+4s window, so the root gets a longer runway:
# backoff 1+2+4+8+16s ≈ 31s worst case.
ROOT_FETCH_ATTEMPTS = 6
# Cap on a single retry wait — one host must not stall a validation run for
# minutes if it sends a huge Retry-After.
_MAX_RETRY_WAIT = 30.0


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Parse ``Retry-After`` (delta-seconds or HTTP-date); None if absent/broken."""
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            delta = parsedate_to_datetime(value) - datetime.now(timezone.utc)
            return max(0.0, delta.total_seconds())
        except (TypeError, ValueError):
            return None


# Redirects are followed manually (one request per hop) so the SSRF guard
# re-validates every hop's target — auto-following would let a public URL
# 302 into loopback/private space unchecked.
_MAX_REDIRECT_HOPS = 10


async def _guarded_get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str] | None,
    timeout: int,
) -> httpx.Response:
    """GET ``url`` with every hop validated and host-pinned."""
    next_url = url
    for hop in range(_MAX_REDIRECT_HOPS + 1):
        # Re-validated every hop — redirects are followed manually so a
        # public URL cannot 302 into loopback/private space unchecked.
        next_url = await public_http_url(next_url)
        # Host pin: a redirect may not leave the host of the requested URL.
        if not same_http_host(url, next_url):
            raise UnsafeUrlError(
                f"Redirect leaves the original host of {url}"
            )
        resp = await client.get(next_url, headers=headers, timeout=timeout, follow_redirects=False)  # codeql[py/full-ssrf] — every hop validated & host-pinned above
        if not resp.is_redirect:
            return resp
        if hop == _MAX_REDIRECT_HOPS:
            raise httpx.TooManyRedirects(
                f"more than {_MAX_REDIRECT_HOPS} redirects fetching {url}"
            )
        next_request = resp.next_request
        if next_request is None:
            return resp
        next_url = str(next_request.url)
    raise AssertionError("unreachable: redirect loop must return or raise")


# ─── Throttling-aware read ──────────────────────────────────────────────────


async def read_stac(
    url: str,
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = _MAX_FETCH_ATTEMPTS,
) -> dict:
    """GET one STAC object (or any JSON document), retrying 429/5xx responses.

    A 429 honors the server's ``Retry-After`` header (delta-seconds or
    HTTP-date); without it, backoff is exponential (1s, 2s, 4s), capped at
    ``_MAX_RETRY_WAIT``. The final attempt's failure propagates unchanged so
    callers keep their existing error mapping. ``max_attempts`` extends the
    retry budget (the root fetch uses :data:`ROOT_FETCH_ATTEMPTS`).
    """
    for attempt in range(max_attempts):
        last = attempt == max_attempts - 1
        t0 = time.perf_counter()
        try:
            resp = await _guarded_get(client, url, headers, timeout)
            if resp.status_code in _RETRYABLE_STATUS and not last:
                retry_after = _retry_after_seconds(resp)
                wait = min(
                    retry_after if retry_after is not None else 2**attempt,
                    _MAX_RETRY_WAIT,
                )
                logger.warning(
                    "Upstream throttled or unavailable — retrying",
                    extra={
                        "url": url,
                        "status_code": resp.status_code,
                        "attempt": attempt + 1,
                        "wait_seconds": round(wait, 2),
                    },
                )
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            logger.debug(
                "FETCH OK: %s (%d bytes, %.3fs, attempt %d, status %d)",
                url,
                len(resp.content),
                time.perf_counter() - t0,
                attempt + 1,
                resp.status_code,
            )
            return resp.json()
        except httpx.TransportError:
            if last:
                raise
            wait = min(2**attempt, _MAX_RETRY_WAIT)
            logger.warning(
                "Transport error — retrying",
                extra={
                    "url": url,
                    "attempt": attempt + 1,
                    "wait_seconds": wait,
                },
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable: retry loop must return or raise")


async def resolve_base(
    stac_json: dict[str, Any] | None,
    source_url: str | None,
    *,
    client: httpx.AsyncClient,
    access_token: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a builder's base object: inline ``stac_json``, else fetch
    ``source_url`` (SSRF-guarded, bearer attached when a token is given),
    else None. Shared by the STAC builders' fields/overlay input modes."""

    if stac_json is not None:
        return dict(stac_json)
    if source_url is not None:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
        return await read_stac(str(source_url), client=client, headers=headers)
    return None


class StacObjectType(str, Enum):
    CATALOG = "catalog"
    COLLECTION = "collection"
    ITEM = "item"
    ITEM_COLLECTION = "item_collection"
    UNKNOWN = "unknown"


def get_stac_object_type(data: dict) -> StacObjectType:
    """Map a raw STAC JSON object to our Enum."""
    t = data.get("type")
    if t == "Feature":
        return StacObjectType.ITEM
    if t == "FeatureCollection":
        return StacObjectType.ITEM_COLLECTION
    if t == "Collection":
        return StacObjectType.COLLECTION
    if t == "Catalog":
        return StacObjectType.CATALOG
    if isinstance(data.get("assets"), dict):
        return StacObjectType.ITEM
    return StacObjectType.UNKNOWN


# ─── Shared fetch primitive ─────────────────────────────────────────────────


# One BFS queue entry: (url, depth, parent_type, parent_url).
QueueEntry = tuple[str, int, Optional[StacObjectType], Optional[str]]

# Fetch one queue entry: parsed objects for the URL plus its raw JSON
# (None on fetch failure).
FetchFn = Callable[
    [QueueEntry], Awaitable[tuple[list["CollectedObject"], dict | None]]
]

# Discovery policy for one fetched entry: queue entries for its
# children/pagination pages. ``raw`` is the fetched JSON (never None — the
# BFS loop skips fetch failures before discovery).
DiscoverFn = Callable[
    [QueueEntry, dict, list["CollectedObject"]], list[QueueEntry]
]


@dataclass
class CollectedObject:
    """One STAC object fetched during Breadth-First Search, with traversal context.

    ``fetch_error`` is set when the HTTP fetch or JSON parse failed; ``data``
    is ``None`` in that case. The caller decides how to surface errors
    (e.g. create an error node in a validation tree).
    """

    data: dict | None
    url: str
    obj_type: StacObjectType
    parent_type: Optional[StacObjectType]
    parent_url: Optional[str]
    depth: int
    fetch_error: Exception | None = None


async def fetch_stac_objects(
    url: str,
    depth: int,
    parent_type: Optional[StacObjectType],
    parent_url: Optional[str],
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    timeout: int,
    semaphore: asyncio.Semaphore,
    max_attempts: int = _MAX_FETCH_ATTEMPTS,
) -> tuple[list[CollectedObject], dict | None]:
    """Fetch one URL and return its STAC object(s) as raw dicts plus the raw JSON.

    Error handling — nothing is raised for per-object failures; the caller
    decides how to surface them (e.g. error nodes in a validation tree):
    - HTTP fetch / JSON parse failure → ``([CollectedObject(fetch_error=…)], None)``

    Two STAC API payload shapes get special treatment:
    - ``/collections`` listing pages (top-level ``collections`` array) are not
      STAC objects — returned as ``([], raw)`` so API traversal can extract
      collection URLs and pagination.
    - ``/items`` pages (``type == "FeatureCollection"``) yield one
      ``CollectedObject`` per feature, so validation treats them like
      statically linked items.
    """

    def _error(exc: Exception) -> CollectedObject:
        return CollectedObject(
            data=None,
            url=url,
            obj_type=StacObjectType.UNKNOWN,
            parent_type=parent_type,
            parent_url=parent_url,
            depth=depth,
            fetch_error=exc,
        )

    async with semaphore:
        # The retry (including its backoff sleep) runs inside the semaphore:
        # backoff for a throttled host also throttles our overall request
        # rate, which is the desired behavior.
        try:
            data = await read_stac(
                url,
                client=client,
                headers=headers,
                timeout=timeout,
                max_attempts=max_attempts,
            )
        except Exception as exc:
            logger.warning(
                "Fetch failed for STAC object",
                extra={"url": url, "depth": depth, "error": str(exc)},
            )
            return [_error(exc)], None

    if not isinstance(data, dict):
        return (
            [_error(ValueError(f"Expected a JSON object, got {type(data).__name__}"))],
            None,
        )

    # STAC API /collections listing page: not a STAC object itself.
    if isinstance(data.get("collections"), list):
        return [], data

    # STAC API /items page: yield each feature as its own Item. All features
    # share the page URL, so CollectedObject.url uses the feature's rel="self"
    # link (or a fragment) to stay unique — the caller keys tree nodes by URL.
    if data.get("type") == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            features = []
        objs: list[CollectedObject] = []
        for index, feature in enumerate(features):
            if not isinstance(feature, dict):
                objs.append(_error(ValueError(f"Feature {index} is not a JSON object")))
                continue
            self_href = next(
                (
                    link["href"]
                    for link in feature.get("links", [])
                    if isinstance(link, dict)
                    and link.get("rel") == "self"
                    and link.get("href")
                ),
                None,
            )
            item_url = (
                urljoin(url, self_href)
                if self_href
                else f"{url}#items/{feature.get('id', index)}"
            )
            objs.append(
                CollectedObject(
                    data=feature,
                    url=item_url,
                    obj_type=get_stac_object_type(feature),
                    parent_type=parent_type,
                    parent_url=parent_url,
                    depth=depth,
                )
            )
        return objs, data

    obj_type = get_stac_object_type(data)
    logger.debug(
        "RESOLVED: %s → %s id=%r stac_version=%s depth=%d",
        url,
        obj_type.value,
        data.get("id"),
        data.get("stac_version") or "?",
        depth,
    )
    return (
        [
            CollectedObject(
                data=data,
                url=url,
                obj_type=obj_type,
                parent_type=parent_type,
                parent_url=parent_url,
                depth=depth,
            )
        ],
        data,
    )


# ─── Link extraction ────────────────────────────────────────────────────────


def _next_page_urls(url: str, raw: dict) -> list[str]:
    """Absolute hrefs of ``rel="next"`` links on a paged API response."""
    return [
        urljoin(url, link["href"])
        for link in raw.get("links", [])
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href")
    ]


def _linked_items_url(base_url: str, raw: dict | None) -> str | None:
    """Absolute ``rel="items"`` link from a collection payload, if present."""
    for link in (raw or {}).get("links", []):
        if (
            isinstance(link, dict)
            and link.get("rel") == "items"
            and link.get("href")
        ):
            return urljoin(base_url, link["href"])
    return None


def static_children(
    data: dict,
    url: str,
    depth: int,
    obj_type: StacObjectType,
) -> list[tuple[str, int, StacObjectType, str]]:
    """Resolve a catalog/collection's static child+item links to queue entries.

    Link-driven rather than type-driven: ``rel="child"``/``rel="item"`` links
    are followed whenever present, so catalogs that omit ``"type": "Catalog"``
    still traverse.
    """
    out: list[tuple[str, int, StacObjectType, str]] = []
    for link in data.get("links", []):
        if (
            isinstance(link, dict)
            and link.get("rel") in ("child", "item")
            and link.get("href")
        ):
            out.append((urljoin(url, link["href"]), depth + 1, obj_type, url))
    return out


# ─── Async BFS tree collection (streaming) ─────────────────────────────────


async def _bfs_batches(
    queue: deque[QueueEntry],
    visited: set[str],
    *,
    fetch: FetchFn,
    discover: DiscoverFn,
    max_objects: int,
    max_concurrency: int,
    same_host_as: str | None = None,
) -> AsyncGenerator[list[CollectedObject], None]:
    """Shared BFS mechanics: drain the queue, fetch a batch concurrently,
    let discovery enqueue children/pages, yield the batch.

    Traversal policy (depth limits, collect_children, link semantics) lives
    in the caller's ``discover`` closure; this loop enforces only the shared
    invariants: dedupe by URL, stop at ``max_objects``, batches of at most
    ``max_concurrency`` fetched via ``asyncio.gather`` — and, when
    ``same_host_as`` is given, that no discovered URL leaves the catalog's
    host (SSRF containment: a catalog cannot redirect traversal elsewhere).

    **Why a generator, not a list?** The caller can start processing
    (validating) each batch immediately while the next batch's HTTP requests
    are in flight.
    """
    batch_index = 0
    skipped: set[str] = set()
    while queue and len(visited) < max_objects:
        batch: list[QueueEntry] = []
        while queue and len(batch) < max_concurrency and len(visited) < max_objects:
            entry = queue.popleft()
            if entry[0] in visited:
                continue
            if same_host_as is not None and not same_http_host(
                same_host_as, entry[0]
            ):
                logger.warning(
                    "Cross-host link skipped — traversal is pinned to the catalog host",
                    extra={"url": entry[0], "catalog_host": same_host_as},
                )
                skipped.add(entry[0])
                continue
            visited.add(entry[0])
            batch.append(entry)
        if not batch:
            break

        results = await asyncio.gather(*(fetch(entry) for entry in batch))

        batch_objs: list[CollectedObject] = []
        for entry, (objs, raw) in zip(batch, results):
            batch_objs.extend(objs)
            if raw is None:
                continue
            try:
                children = discover(entry, raw, objs)
            except Exception as exc:
                # Last-resort guard: a broken discover() must not abort the
                # traversal. discover() implementations do their own finer-
                # grained guarding where it matters.
                logger.warning(
                    "Link discovery failed, skipping children",
                    extra={"url": entry[0], "depth": entry[1], "error": str(exc)},
                )
                continue
            for child in children:
                if child[0] not in visited and child[0] not in skipped:
                    queue.append(child)

        batch_index += 1
        logger.debug(
            "BATCH %d yielded: %d objects, queue=%d, visited=%d",
            batch_index,
            len(batch_objs),
            len(queue),
            len(visited),
        )

        # Yield this batch to the caller, which can process it (in a thread)
        # while we fetch the next batch on the next async-for iteration.
        yield batch_objs

    logger.info(
        "STAC tree collection complete",
        extra={"total_visited": len(visited), "total_batches": batch_index},
    )


# ─── STAC API traversal (OGC API - Features / STAC API - Collections) ──────


def is_stac_api_catalog(data: dict) -> bool:
    """True when the raw root JSON advertises STAC API conformance or links."""
    if data.get("conformsTo"):
        return True
    return any(
        isinstance(link, dict) and link.get("rel") == "data"
        for link in data.get("links", [])
    )


def _collections_endpoint_urls(
    root: CollectedObject, root_raw: dict | None
) -> list[str]:
    """Seed URLs for the API's collections listing.

    Prefers the root's ``rel="data"`` link; falls back to the canonical
    ``{root}/collections``.
    """
    urls = [
        urljoin(root.url, link["href"])
        for link in (root_raw or {}).get("links", [])
        if isinstance(link, dict) and link.get("rel") == "data" and link.get("href")
    ]
    return urls or [root.url.rstrip("/") + "/collections"]


def _collection_url(entry: dict, page_url: str) -> str | None:
    """Canonical URL of one collection inside a ``/collections`` page.

    Prefers the entry's ``rel="self"`` link; falls back to the canonical
    ``{base}/collections/{id}`` layout derived from the page URL.
    """
    for link in entry.get("links", []):
        if (
            isinstance(link, dict)
            and link.get("rel") == "self"
            and link.get("href")
        ):
            return urljoin(page_url, link["href"])
    cid = entry.get("id")
    if not cid:
        return None
    base = page_url.split("?", 1)[0].rstrip("/")
    if base.endswith("/collections"):
        base = base[: -len("/collections")]
    return f"{base}/collections/{quote(str(cid), safe='')}"


def _items_url(obj: CollectedObject, raw: dict | None) -> str:
    """Items endpoint of one collection (``rel="items"`` link or ``{url}/items``)."""
    return _linked_items_url(obj.url, raw) or obj.url.rstrip("/") + "/items"


async def read_stac_api(
    url: str,
    *,
    client: httpx.AsyncClient,
    headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
    root: CollectedObject | None = None,
    root_raw: dict | None = None,
    max_depth: int = 3,
    max_objects: int = 5000,
    collect_children: bool = True,
    max_concurrency: int = 20,
    same_host_as: str | None = None,
) -> AsyncGenerator[list[CollectedObject], None]:
    """Async-iterate a STAC API catalog tree; yields batches of fetched objects.

    BFS over STAC API endpoints (``/collections``, ``/collections/{id}/items``)
    with ``rel="next"`` pagination. Depth mapping: root 0 → collections 1 →
    items 2, matching static traversal. Pagination continues regardless of
    ``max_depth`` (pages are the same level, not children).

    Discovery is hybrid: API endpoints (``/collections``, ``/items``) are used
    when available, and static ``child``/``item`` links are followed too —
    real APIs (e.g. EDITO) mix both styles within one catalog.

    Root contract — the caller either:
    - passes ``root``/``root_raw`` it already fetched **and yielded** (callers
      that must inspect the root to detect API mode before delegating here,
      e.g. ``collect_stac_tree``), in which case those batches are NOT
      re-yielded; or
    - passes only ``url``, in which case the root is fetched here (with the
      extended :data:`ROOT_FETCH_ATTEMPTS` budget) and yielded as the first
      batch, so the "root is in the first batch" contract holds.
    """
    request_headers = headers or {}
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch(
        entry: QueueEntry,
    ) -> tuple[list[CollectedObject], dict | None]:
        entry_url, depth, ptype, purl = entry
        return await fetch_stac_objects(
            entry_url,
            depth,
            ptype,
            purl,
            client=client,
            headers=request_headers,
            timeout=timeout,
            semaphore=semaphore,
        )

    if root is None:
        root_objs, root_raw = await fetch_stac_objects(
            url,
            0,
            None,
            None,
            client=client,
            headers=request_headers,
            timeout=timeout,
            semaphore=semaphore,
            max_attempts=ROOT_FETCH_ATTEMPTS,
        )
        root = root_objs[0] if root_objs else None
        yield root_objs
        if root is None or root.data is None:
            return

    # visited counts URLs, not objects — FeatureCollection pages yield
    # one object per feature, so max_objects is approximate for large item pages.
    visited: set[str] = {root.url}
    queue: deque[QueueEntry] = deque(
        (u, 1, StacObjectType.CATALOG, root.url)
        for u in _collections_endpoint_urls(root, root_raw)
    )
    # Hybrid discovery also starts at the root: its static child/item links
    # must be seeded here, because the root is fetched by collect_stac_tree
    # (outside this BFS) and would otherwise never get its static links walked.
    if collect_children and root.data is not None:
        for entry in static_children(root.data, root.url, root.depth, root.obj_type):
            if entry[0] not in visited:
                queue.append(entry)

    def discover(
        entry: QueueEntry, raw: dict, objs: list[CollectedObject]
    ) -> list[QueueEntry]:
        page_url, depth, ptype, purl = entry
        out: list[QueueEntry] = []
        for href in _next_page_urls(page_url, raw):
            out.append((href, depth, ptype, purl))
        if not collect_children or depth >= max_depth:
            return out
        # A /collections page: enqueue each collection's canonical URL so
        # it is fetched (and validated) as its own object, like the static
        # traversal does. The page itself is not a STAC object.
        if isinstance(raw.get("collections"), list):
            for page_entry in raw["collections"]:
                cu = (
                    _collection_url(page_entry, page_url)
                    if isinstance(page_entry, dict)
                    else None
                )
                if cu:
                    # Parent is the entry that led here (the catalog), not
                    # the page — pages never become tree nodes.
                    out.append((cu, depth, ptype, purl))
        for obj in objs:
            if obj.data is None:
                continue
            if obj.obj_type is StacObjectType.COLLECTION:
                iu = _items_url(obj, raw)
                out.append((iu, depth + 1, StacObjectType.COLLECTION, obj.url))
            # Hybrid: follow static child/item links as well — some APIs
            # nest plain catalogs (with child links) under API roots.
            out.extend(static_children(obj.data, obj.url, obj.depth, obj.obj_type))
        return out

    async for batch in _bfs_batches(
        queue,
        visited,
        fetch=fetch,
        discover=discover,
        max_objects=max_objects,
        max_concurrency=max_concurrency,
        same_host_as=same_host_as,
    ):
        yield batch


# ─── STAC API search ────────────────────────────────────────────────────────


async def search_stac_api(
    url: str,
    *,
    client: httpx.AsyncClient,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """POST one STAC API ``/search`` request; returns the raw ItemCollection JSON.

    Minimal primitive: a single POST with ``raise_for_status`` semantics.
    Paging, filtering policy, and error mapping belong to the caller.
    """
    url = await public_http_url(url)
    resp = await client.post(url, json=body if body is not None else {}, headers=headers, timeout=timeout)  # codeql[py/full-ssrf] — URL validated above
    resp.raise_for_status()
    return resp.json()
