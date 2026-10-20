"""Zarr metadata → STAC Item orchestration.

Pipeline: summarize the source (infrastructure) → resolve the item id
(explicit, else the dataset's own ``id``/``identifier``/``title`` attribute
or the store name, namespaced with the caller's username) → resolve the
output target → resolve the collection (only an explicit ``collection_id``
is attached, used as-is; otherwise the item is standalone and a suggestion
is returned) → build the item (adapters) → write it (adapters). All
translation lives in ``adapters.py``.

Output follows the toolkit convention: S3 by default —
``s3://<bucket>/<target>/<item_id>.json`` with ``bucket`` defaulting to the
caller's personal ``oidc-<username>`` bucket — or an explicit
``local_output`` directory. The file name is derived from the item id unless
the target already ends in one (see :func:`ept.infrastructure.storage.s3.with_file_name`).

Collection suggestions prefer official vocabularies — CF Conventions
``standard_name`` (https://cfconventions.org) or BODC P01 parameter codes
(https://vocab.nerc.ac.uk/collection/P01/current/) — so suggested names can
be matched automatically.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.concurrency import run_in_threadpool

from ept.infrastructure.formats.zarr import DatasetSummary, summarize_zarr
from ept.infrastructure.services.editostacapi import EditoStacApiClient
from ept.infrastructure.storage.s3 import (
    is_s3_uri,
    resolve_s3_storage_options,
    safe_export_name,
    with_file_name,
)

from . import adapters
from .models import DatasetSummaryModel, ZarrToStacItemRequest, ZarrToStacItemResponse

logger = logging.getLogger(__name__)

COLLECTIONS_PATH = "/collections"
MAX_COLLECTION_PAGES = 10


def resolve_target_uri(
    request: ZarrToStacItemRequest, item_id: str, default_bucket: str | None
) -> tuple[str, bool]:
    """Full output path plus whether it is S3 (drives storage options).

    S3 is the standard output: ``s3://<bucket>/<target>/<item_id>.json`` with
    the bucket defaulting to the caller's personal one. ``local_output``
    overrides with a directory on the EPT filesystem. In both cases the file
    name is the resolved item id + ``.json`` unless the target already ends
    in one.
    """

    file_name = f"{safe_export_name(item_id, fallback='item')}.json"
    if request.local_output:
        return with_file_name(request.local_output, file_name), False
    bucket = request.bucket or default_bucket
    if not bucket:
        raise ValueError(
            "bucket is required: the bearer token carries no username from which to "
            "derive the default oidc-<username> bucket."
        )
    target = request.target
    if not target:  # validate_output_choice guarantees one of target/local_output
        raise ValueError(
            "Exactly one output is required: `target` (S3 prefix within "
            "`bucket`) or `local_output` (a directory on the EPT filesystem)."
        )
    return f"s3://{bucket}/{with_file_name(target, file_name)}", True


async def zarr_to_stac_item(
    request: ZarrToStacItemRequest,
    *,
    access_token: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    default_bucket: str | None = None,
    default_username: str | None = None,
) -> ZarrToStacItemResponse:
    """Build a STAC Item from Zarr metadata and write it to the target."""
    storage_options = resolve_s3_storage_options(request) if is_s3_uri(request.source_uri) else None

    # 1. Zarr metadata first: the default item id prefers the dataset's own
    #    identifier attribute, so the summary must exist before the output
    #    path (which names the file after the id) is resolved.
    summary = await run_in_threadpool(
        summarize_zarr,
        request.source_uri,
        storage_options=storage_options,
        decode_cf=request.decode_cf,
        consolidated=request.consolidated,
    )
    logger.info(
        "Zarr summary source=%s variables=%s standard_names=%s bbox=%s temporal=%s instant=%s",
        request.source_uri,
        list(summary.variables),
        summary.standard_names,
        summary.bbox,
        summary.temporal,
        summary.instant,
    )

    # 1.5 Item id: explicit, else dataset identifier/store name namespaced
    #     with the caller's username (same scheme as the suggesters).
    item_id = request.item_id or adapters.proposed_item_id(summary, request.source_uri, default_username)

    # 2. Output target (names the file after the item id).
    target_uri, output_is_s3 = resolve_target_uri(request, item_id, default_bucket)
    if output_is_s3 and storage_options is None:
        storage_options = resolve_s3_storage_options(request)

    # 3. Fail fast on missing extents (ValueError → 400 with guidance naming
    #     the request fields to set) before any EDITO STAC API round-trips.
    await run_in_threadpool(adapters.resolve_extents, request, summary)

    # 4. Collection: the provided one, or standalone + a suggested id (the
    #    item is never attached to a collection automatically).
    owns_client = http_client is None
    if owns_client:
        http_client = httpx.AsyncClient(timeout=30.0)
    try:
        collection_id, collection_source, suggested_collection_id = await resolve_collection(
            request, summary, access_token=access_token, http_client=http_client
        )
    finally:
        if owns_client:
            await http_client.aclose()

    # 5. Build (CPU) and write (I/O) — both off the event loop.
    item = await run_in_threadpool(adapters.build_item, request, summary, collection_id, item_id)
    written_uri = await run_in_threadpool(
        adapters.write_item,
        item,
        target_uri,
        storage_options=storage_options,
        overwrite=request.overwrite,
    )
    logger.info(
        "Zarr to STAC item completed source=%s target=%s collection=%s (%s)",
        request.source_uri, written_uri, collection_id, collection_source,
    )
    return ZarrToStacItemResponse(
        stac_item=item,
        target_uri=written_uri,
        collection_id=collection_id,
        collection_source=collection_source,
        suggested_collection_id=suggested_collection_id,
        dataset=DatasetSummaryModel.from_infra(summary),
    )


# ─── Collection resolution (EDITO STAC API orchestration) ───────────────────


async def resolve_collection(
    request: ZarrToStacItemRequest,
    summary: DatasetSummary,
    *,
    access_token: str | None,
    http_client: httpx.AsyncClient,
) -> tuple[str | None, str, str | None]:
    """Return ``(collection_id | None, source, suggested_collection_id | None)``.

    An explicit ``collection_id`` is used as-is — it does not have to exist in
    the EDITO STAC API (creating/publishing the collection is the caller's
    workflow). Otherwise the item is built standalone — never auto-attached —
    and a suggestion is returned instead: an existing EDITO STAC collection
    whose text matches the dataset's vocabulary
    (:func:`adapters.find_matching_collection`), falling back to a kebab-case
    id from the CF ``standard_name``.
    """
    if request.collection_id:
        return request.collection_id, "provided", None

    collections = await _load_collections(access_token=access_token, http_client=http_client)
    logger.info(
        "Loaded EDITO STAC collections count=%d ids=%s",
        len(collections),
        [c.get("id") for c in collections][:20],
    )
    tokens = adapters.collection_tokens(summary)
    logger.info("Suggesting a collection for dataset tokens=%s", tokens)
    matched = adapters.find_matching_collection(summary, collections)
    if matched is not None:
        suggestion = str(matched["id"])
        logger.info("Suggested existing EDITO STAC collection id=%s (not attached)", suggestion)
    else:
        suggestion = adapters.suggested_collection_id(summary)
        logger.info(
            "No EDITO STAC collection matched; suggested_collection_id=%s (not attached)",
            suggestion,
        )
    return None, "standalone", suggestion


async def _load_collections(
    *, access_token: str | None, http_client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    """Fetch all EDITO STAC collections, following ``rel="next"`` pages."""
    client = EditoStacApiClient(http_client)
    collections: list[dict[str, Any]] = []
    path: str | None = COLLECTIONS_PATH
    for _ in range(MAX_COLLECTION_PAGES):
        if path is None:
            break
        payload = await client.get_json(path, access_token=access_token)
        page = payload if isinstance(payload, dict) else {}
        collections.extend(e for e in page.get("collections", []) if isinstance(e, dict))
        path = _next_path(page.get("links", []))
    return collections


def _next_path(links: Any) -> str | None:
    """Path (+query) of the page's ``rel="next"`` link, or None."""
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and link.get("rel") == "next" and link.get("href"):
            parts = urlsplit(str(link["href"]))
            return f"{parts.path}?{parts.query}" if parts.query else parts.path
    return None
