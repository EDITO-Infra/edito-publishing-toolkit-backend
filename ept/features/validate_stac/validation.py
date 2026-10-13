"""STAC validation policy and traversal orchestration (feature-local).

All validation/traversal policy lives here; the thin HTTP primitives it
composes live in ``ept.infrastructure.formats.stac`` (retrying JSON reads,
STAC API tree traversal, link extraction, BFS mechanics). This module owns:

- the latest-version gate (``get_latest_stac_version`` / ``StacVersionError``)
- schema validation (``StacSchemaValidator``, wrapping stac-validator)
- the feature-facing transport error (``StacFetchError``)
- tree traversal orchestration (``collect_stac_tree``): fetch the root, decide
  static-catalog vs STAC API mode, then BFS accordingly

Per-object EDITO rules live in ``adapter.py``; together the two modules form
the feature's validation layer.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
import logging

import httpx
from stac_validator.fast_validator import FastValidator

from ept.infrastructure.formats.stac import (
    ROOT_FETCH_ATTEMPTS,
    CollectedObject,
    QueueEntry,
    StacObjectType,
    _bfs_batches,
    _linked_items_url,
    _next_page_urls,
    fetch_stac_objects,
    is_stac_api_catalog,
    read_stac_api,
    static_children,
)

logger = logging.getLogger(__name__)


class StacFetchError(Exception):
    """Fetching a remote STAC object failed.

    ``status_code`` mirrors the upstream HTTP status, or ``None`` when there was
    no usable upstream response (transport/parse failure). Callers decide the
    public status to surface for the ``None`` case (e.g. 502).
    """

    def __init__(self, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class StacVersionError(Exception):
    """A STAC object declares a ``stac_version`` older than the latest spec.

    Validation only supports the latest STAC version: the run stops at the
    first object that declares an older one and asks for an upgrade.
    ``status_code`` is the HTTP surface chosen by the route layer (422).
    """

    def __init__(self, found: str, latest: str, url: str | None = None) -> None:
        self.found = found
        self.latest = latest
        self.url = url
        self.status_code = 422
        self.message = (
            f"STAC version {found} is not supported; the latest STAC version "
            f"is {latest}. Upgrade the catalog to STAC {latest} and retry."
            + (f" (first offending object: {url})" if url else "")
        )
        super().__init__(self.message)


# ─── Latest-version gate ───────────────────────────────────────────────────


# Used when the live lookup fails (no network, GitHub rate limit): validation
# stays pinned to the newest known spec release instead of erroring out.
LATEST_STAC_VERSION_FALLBACK = "1.1.0"
_STAC_RELEASES_URL = (
    "https://api.github.com/repos/radiantearth/stac-spec/releases/latest"
)


async def get_latest_stac_version(client: httpx.AsyncClient) -> str:
    """Latest STAC spec release version, from the GitHub releases API.

    Falls back to :data:`LATEST_STAC_VERSION_FALLBACK` (with a warning) when
    GitHub is unreachable or rate-limited, so validation still runs pinned to
    the newest known spec release.
    """
    try:
        resp = await client.get(_STAC_RELEASES_URL, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        tag = resp.json().get("tag_name")
        if isinstance(tag, str) and tag:
            return tag.removeprefix("v")
    except Exception as exc:
        logger.warning(
            "Could not fetch latest STAC version from GitHub — using fallback",
            extra={"url": _STAC_RELEASES_URL, "fallback": LATEST_STAC_VERSION_FALLBACK,
                   "error": str(exc)},
        )
    return LATEST_STAC_VERSION_FALLBACK


# ─── Tree traversal orchestration (streaming) ──────────────────────────────


async def collect_stac_tree(
    root_url: str,
    access_token: str | None,
    timeout: int,
    http_client: httpx.AsyncClient,
    max_depth: int,
    collect_children: bool,
    max_objects: int = 5000,
    max_concurrency: int = 20,
) -> AsyncGenerator[list[CollectedObject], None]:
    """Async Breadth-First Search (BFS) traversal — yields batches of fetched objects as they complete.

    Returns an async generator that yields one ``list[CollectedObject]`` per
    BFS batch. Each batch contains up to ``max_concurrency`` objects fetched
    concurrently with ``asyncio.gather`` under a semaphore.

    The root is fetched first and yielded as its own batch; if it advertises
    STAC API conformance (``conformsTo`` or ``rel="data"``), traversal is
    delegated to :func:`ept.infrastructure.formats.stac.read_stac_api`
    (endpoint-based); otherwise static ``child``/``item`` links are followed.

    **Why a generator, not a list?** The caller can start processing
    (validating) each batch immediately while the next batch's HTTP requests
    are in flight. This overlaps CPU-bound validation with network-bound
    fetching, which a "fetch-all-then-validate" approach cannot do.

    **Error handling — nothing aborts the traversal:** per-object fetch and
    parse failures are captured on the ``CollectedObject`` (see
    ``fetch_stac_objects``); link extraction failures skip that object's
    children only. The caller (service layer) checks the first batch's root
    object and raises ``StacFetchError`` if the root itself failed.
    """
    logger.info(
        "Starting STAC tree collection",
        extra={
            "root_url": root_url,
            "max_depth": max_depth,
            "max_objects": max_objects,
            "max_concurrency": max_concurrency,
            "collect_children": collect_children,
        },
    )

    # Bearer token rides on every GET — required for EDITO STAC API URLs,
    # never attached elsewhere (the caller decides).
    headers = (
        {"Authorization": f"Bearer {access_token}"} if access_token else {}
    )
    semaphore = asyncio.Semaphore(max_concurrency)

    async def fetch(
        entry: QueueEntry,
    ) -> tuple[list[CollectedObject], dict | None]:
        url, depth, ptype, purl = entry
        return await fetch_stac_objects(
            url,
            depth,
            ptype,
            purl,
            client=http_client,
            headers=headers,
            timeout=timeout,
            semaphore=semaphore,
        )

    # Fetch the root up front: traversal mode (static catalog vs STAC API) is
    # decided from the root object. The root is yielded as its own first
    # batch, so the caller's "root is in the first batch" contract holds.
    root_objs, root_raw = await fetch_stac_objects(
        root_url,
        0,
        None,
        None,
        client=http_client,
        headers=headers,
        timeout=timeout,
        semaphore=semaphore,
        max_attempts=ROOT_FETCH_ATTEMPTS,
    )
    yield root_objs

    if not collect_children or not root_objs:
        return
    root = root_objs[0]
    if root.data is None:
        return
    if is_stac_api_catalog(root.data):
        # The root has already been fetched and yielded above; hand it to the
        # API traversal so it is not requested twice.
        async for batch in read_stac_api(
            root_url,
            client=http_client,
            headers=headers,
            timeout=timeout,
            root=root,
            root_raw=root_raw,
            max_depth=max_depth,
            max_objects=max_objects,
            collect_children=collect_children,
            max_concurrency=max_concurrency,
            same_host_as=root_url,
        ):
            yield batch
        return

    # ── Static catalog BFS (child/item links) ───────────────────────────────

    visited: set[str] = {root_url}
    queue: deque[QueueEntry] = deque()
    for child in static_children(root.data, root.url, 0, root.obj_type):
        if child[0] not in visited:
            queue.append(child)

    def discover(
        entry: QueueEntry, raw: dict, objs: list[CollectedObject]
    ) -> list[QueueEntry]:
        url, depth, ptype, purl = entry
        out: list[QueueEntry] = []
        # rel="next" pagination — followed at the same level, ungated by
        # max_depth (a next page is a sibling of the current page, not a
        # child). Covers API-style items pages reached via rel="items".
        out.extend((href, depth, ptype, purl) for href in _next_page_urls(url, raw))
        if depth >= max_depth:
            return out
        for result in objs:
            if result.fetch_error or result.data is None:
                continue
            try:
                out.extend(
                    static_children(
                        result.data, result.url, result.depth, result.obj_type
                    )
                )
                # Collections may expose items only via rel="items"
                # (API-style) instead of item links — follow that link.
                if result.obj_type is StacObjectType.COLLECTION:
                    items_url_ = _linked_items_url(result.url, raw)
                    if items_url_:
                        out.append(
                            (items_url_, depth + 1, result.obj_type, result.url)
                        )
            except Exception as exc:
                # Link extraction can fail on malformed catalogs. Log and
                # continue — siblings are unaffected.
                logger.warning(
                    "Link extraction failed, skipping children",
                    extra={
                        "url": result.url,
                        "depth": result.depth,
                        "error": str(exc),
                    },
                )
        return out

    async for batch in _bfs_batches(
        queue,
        visited,
        fetch=fetch,
        discover=discover,
        max_objects=max_objects,
        max_concurrency=max_concurrency,
        same_host_as=root_url,
    ):
        yield batch


# ─── Schema validation ─────────────────────────────────────────────────────


class StacSchemaValidator:
    """Wraps stac-validator's ``FastValidator`` for reuse across many objects.

    One instance = one warm cache. The compiled ``fastjsonschema`` validators
    stay in RAM for the lifetime of the instance, and the disk cache
    (``local_schemas/.schemas/``) persists across instances and process
    restarts.

    **Thread safety:** ``FastValidator`` stores results in mutable instance
    attributes (``self.valid``, ``self.message``), so concurrent calls on the
    same instance are NOT safe. The service layer runs validation in a thread
    via ``asyncio.to_thread`` but awaits each batch before starting the next,
    so the validator is used by one thread at a time.

    **Crash safety:** ``validate()`` catches all exceptions and returns them
    as error strings, never raises. A single malformed object that crashes
    fastjsonschema does not abort the validation run.

    Usage::

        validator = StacSchemaValidator()
        for obj_dict in objects:
            errors = validator.validate(obj_dict)  # cache warm after first call
    """

    def __init__(self) -> None:
        self._validator = FastValidator(stac_file="in-memory", quiet=True)
        self._cache_warmed = False

    def validate(self, data: dict, source: str = "in-memory") -> list[str]:
        """Validate one raw STAC JSON object against the official schemas.

        Returns an empty list if valid, or a list of human-readable error
        messages if the object violates the STAC spec (core or extensions).
        Never raises — crashes are returned as error strings.
        """
        if not self._cache_warmed:
            logger.debug(
                "Schema validator cache cold — first validation",
                extra={"source": source},
            )

        try:
            self._validator.run_dict(data, source_name=source)
        except Exception as exc:
            logger.error(
                "Schema validation crashed",
                extra={"source": source, "error": str(exc)},
            )
            return [f"STAC schema validation crashed: {exc}"]

        self._cache_warmed = True

        if self._validator.valid:
            logger.debug(
                "Schema validation passed",
                extra={
                    "source": source,
                    "obj_id": data.get("id"),
                },
            )
            return []

        errors = [
            entry["error_message"]
            for report in self._validator.message
            for entry in report.get("errors", [])
        ]
        result = errors or ["STAC Schema Validation Failed"]

        logger.debug(
            "Schema validation failed",
            extra={
                "source": source,
                "obj_id": data.get("id"),
                "error_count": len(result),
            },
        )
        return result
