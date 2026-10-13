"""STAC validation orchestration.

Thin service layer: fetches the tree in batches (async Breadth-First Search(BFS)
generator) and validates each batch while the next batch's HTTP requests are
in flight. This overlaps CPU-bound validation with network-bound fetching.

**Failure design:** No single failure — fetch error, parse error, schema
validation crash, or EDITO rule crash — aborts the validation run. Each
failure is captured as an ERROR-level ``ValidationIssue`` on the relevant
node, and the run continues with the next object. Two exceptions abort the
run fast: (1) the root URL — if it can't be fetched, there's nothing to
validate, so ``StacFetchError`` is raised; (2) the version gate — any object
declaring a ``stac_version`` older than the latest spec raises
``StacVersionError`` and stops the run with an upgrade request. Emergency
brake is the max_objects parameter.

All HTTP primitives live in infrastructure (``ept.infrastructure.formats.stac``);
validation/traversal policy lives in this feature (``validation.py``/``adapter.py``).
This module just wires them together.
"""

from __future__ import annotations

from typing import Optional

import asyncio
import httpx
import logging

from ept.infrastructure.formats.stac import StacObjectType
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .validation import (
    StacFetchError,
    StacSchemaValidator,
    StacVersionError,
    collect_stac_tree,
    get_latest_stac_version,
)

from .models import (
    ObjectTypeSummary,
    ValidateStacRequest,
    ValidateStacResponse,
    ValidationIssue,
    ValidationLevel,
    ValidationSummary,
    ValidationTreeNode,
)
from .adapter import validate_batch

# Re-exported for the route layer's error mapping.
__all__ = ["StacFetchError", "StacVersionError", "validate_stac", "flatten_results"]

logger = logging.getLogger(__name__)

MAX_OBJECTS = 50000  # number of objects to fetch before stopping (overridable in feature params)
MAX_CONCURRENCY = 20  # concurrent HTTP requests during BFS traversal
FETCH_TIMEOUT_SECONDS = 30  # per-request HTTP timeout for fetching STAC objects


# ─── Public entry point ────────────────────────────────────────────────────


async def validate_stac(
    request: ValidateStacRequest,
    access_token: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> ValidateStacResponse:
    """Main service entry point — async end to end, no threadpool needed.

    Fetches and validates STAC objects in an interleaved pipeline:

    1. ``collect_stac_tree()`` yields a batch of fetched objects.
    2. That batch is validated in a thread (``asyncio.to_thread``) while
       the generator fetches the next batch.
    3. Results are accumulated into a validation tree.
    4. After all batches are processed, the tree is aggregated.

    This overlaps the CPU-bound schema validation (~0.25ms per object with
    a warm cache) with the network-bound HTTP fetching (the actual
    bottleneck), so neither blocks the other.

    Args:
        request: Validation request with URL, depth, flags.
        access_token: Bearer token for protected STAC catalogs.
        http_client: Optional pre-configured async HTTP client. If omitted,
            a new one is created and closed after the run.
    """
    owns_client = http_client is None
    if owns_client:
        http_client = httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS)

    try:
        return await _validate(request, access_token, http_client)
    finally:
        if owns_client:
            await http_client.aclose()


# ─── Orchestration: streaming fetch + validate ──────────────────────────────


async def _validate(
    request: ValidateStacRequest,
    access_token: Optional[str],
    http_client: httpx.AsyncClient,
) -> ValidateStacResponse:
    logger.info(
        "STAC validation started",
        extra={
            "stac_url": str(request.stac_url),
            "validate_children": request.validate_children,
            "max_objects": request.max_objects,
            "max_depth": request.max_depth,
        },
    )

    # ── Latest-version gate ─────────────────────────────────────
    # First cutoff: everything must be the latest STAC spec version.
    # Get the latest version up front, then every yielded object is checked
    # against it before any schema/EDITO validation runs. A mismatch aborts
    # the run with an upgrade request.
    latest_version = await get_latest_stac_version(http_client)
    logger.info(
        "Latest STAC version resolved",
        extra={"latest_stac_version": latest_version},
    )

    # One schema validator instance — cache warms after the first object and
    # is reused for every subsequent object. Created here so it persists
    # across all batches.
    schema_validator = StacSchemaValidator()

    # Effective traversal cap: the caller's request cap, never above the
    # feature's hard MAX_OBJECTS brake.
    max_objects = min(request.max_objects, MAX_OBJECTS)

    # Accumulate nodes across batches. Keyed by URL for tree assembly.
    nodes_by_url: dict[str, ValidationTreeNode] = {}
    root_node: Optional[ValidationTreeNode] = None
    root_checked = False
    batch_index = 0

    # Stream batches from the async BFS generator. Each async-for iteration:
    #   1. Yields a batch of fetched CollectedObjects
    #   2. We validate the batch in a thread (non-blocking)
    #   3. The next iteration fetches the next batch concurrently
    #
    # The validation thread and the generator's HTTP fetching run
    # concurrently because asyncio.to_thread returns control to the
    # event loop, which then drives the generator's HTTP requests.
    async for batch in collect_stac_tree(
        root_url=str(request.stac_url),
        access_token=access_token,
        timeout=FETCH_TIMEOUT_SECONDS,
        http_client=http_client,
        max_depth=request.max_depth,
        collect_children=request.validate_children,
        max_objects=max_objects,
        max_concurrency=MAX_CONCURRENCY,
    ):
        batch_index += 1
        logger.debug(
            "Received batch from collector",
            extra={
                "batch_index": batch_index,
                "batch_size": len(batch),
            },
        )

        # ── Root check ────────────────────────────────────────────────
        # The root object is always in the first batch (depth == 0).
        # If it failed to fetch or parse, there's nothing to validate.
        if not root_checked:
            root_checked = True
            root_item = batch[0]
            if root_item.fetch_error:
                _raise_root_fetch_error(
                    root_item.fetch_error, str(request.stac_url)
                )

        # ── Version gate ───────────────────────────────────────────
        # Every object yielded by the BFS must declare the latest STAC
        # version. Raising here abandons the generator, which stops the
        # traversal — no further fetching or validation.
        for item in batch:
            version = (item.data or {}).get("stac_version")
            if isinstance(version, str) and version != latest_version:
                logger.error(
                    "STAC version mismatch — aborting validation",
                    extra={
                        "url": item.url,
                        "found": version,
                        "latest": latest_version,
                    },
                )
                raise StacVersionError(version, latest_version, item.url)

        # ── Validate batch in a thread ────────────────────────────
        # FastValidator is not thread-safe (it stores results in mutable
        # instance attrs), so we never run two validations concurrently —
        # we await each batch before starting the next. The thread runs
        # while the generator fetches the next batch.
        #
        # validate_batch never raises: fetch/parse errors and validator
        # crashes all become ERROR-level issues on the node.
        batch_nodes = await asyncio.to_thread(
            validate_batch,
            batch,
            schema_validator,
        )

        # ── Accumulate nodes ──────────────────────────────────────────
        for url, node in batch_nodes.items():
            nodes_by_url[url] = node

        # Find root node from the batch
        for item in batch:
            if item.depth == 0 and item.url in nodes_by_url:
                root_node = nodes_by_url[item.url]
                break

        # ── Build tree edges for this batch ───────────────────────────
        # Attach each node to its parent. Parents may have been yielded
        # in a previous batch.
        for item in batch:
            if item.parent_url and item.parent_url in nodes_by_url:
                parent = nodes_by_url[item.parent_url]
                parent.children.append(nodes_by_url[item.url])

        # ── Batch summary ─────────────────────────────────────
        valid_count = sum(1 for n in batch_nodes.values() if n.object_is_valid)
        invalid_count = len(batch_nodes) - valid_count
        logger.info(
            "BATCH %d validated: %d valid, %d invalid (%d objects)",
            batch_index,
            valid_count,
            invalid_count,
            len(batch_nodes),
        )

    # hitting the cap exactly is indistinguishable from being cut
    # off at the cap (the collector doesn't expose queue depth) — a catalog
    # with exactly max_objects objects gets a spurious warning. Revisit if
    # the collector ever reports queue state.
    if root_node is not None and len(nodes_by_url) >= max_objects:
        root_node.issues.append(
            ValidationIssue(
                level=ValidationLevel.WARNING,
                object_type=root_node.object_type,
                object_id=root_node.object_id,
                location=root_node.url,
                rule="traversal_limit",
                message=(
                    f"Traversal stopped at the {max_objects}-object limit; "
                    "unvisited children were not validated."
                ),
            )
        )

    if root_node is None:
        raise StacFetchError(None, "No objects collected from root URL")

    # Run-level validity: any ERROR issue anywhere in the tree invalidates the
    # response. Warnings (e.g. traversal_limit) do not invalidate.
    total, errors, warnings = flatten_results(root_node)
    summary = _build_summary(nodes_by_url)

    logger.info(
        "STAC validation complete",
        extra={
            "stac_url": str(request.stac_url),
            "total_objects": total,
            "total_errors": len(errors),
            "total_warnings": len(warnings),
            "stac_is_valid": not errors,
            "summary": summary.model_dump(mode="json"),
        },
    )

    return ValidateStacResponse(
        root_url=str(request.stac_url),
        root_type=root_node.object_type,
        stac_is_valid=not errors,
        total_objects_checked=total,
        summary=summary,
        errors=errors,
        warnings=warnings,
        validation_tree=root_node,
    )


# ─── Aggregation ───────────────────────────────────────────────────────────


def _build_summary(
    nodes_by_url: dict[str, ValidationTreeNode],
) -> ValidationSummary:
    """Per-type valid/invalid counts plus error distribution by rule."""
    by_type: dict[StacObjectType, ObjectTypeSummary] = {}
    errors_by_rule: dict[str, int] = {}
    total = valid = error_count = warning_count = 0

    for node in nodes_by_url.values():
        total += 1
        if node.object_is_valid:
            valid += 1
        bucket = by_type.setdefault(
            node.object_type,
            ObjectTypeSummary(total=0, valid=0, invalid=0),
        )
        bucket.total += 1
        if node.object_is_valid:
            bucket.valid += 1
        else:
            bucket.invalid += 1
        for issue in node.issues:
            if issue.level is ValidationLevel.ERROR:
                error_count += 1
                errors_by_rule[issue.rule] = errors_by_rule.get(issue.rule, 0) + 1
            elif issue.level is ValidationLevel.WARNING:
                warning_count += 1

    return ValidationSummary(
        total_objects=total,
        valid_objects=valid,
        invalid_objects=total - valid,
        by_type=dict(sorted(by_type.items(), key=lambda kv: -kv[1].total)),
        error_count=error_count,
        warning_count=warning_count,
        errors_by_rule=dict(
            sorted(errors_by_rule.items(), key=lambda kv: -kv[1])
        ),
    )


# ─── Tree helpers ────────────────────────────────────────────────────────


def _raise_root_fetch_error(exc: Exception, root_url: str) -> None:
    """Raise ``StacFetchError`` for an unfetchable or unparseable root URL.

    The root is special — if we can't fetch or parse it, there's nothing to
    validate. This is the one case where ``continue_on_error`` does not
    apply: the entire run is aborted.
    """
    logger.error(
        "Root STAC object fetch/parse failed — aborting validation",
        extra={"root_url": root_url, "error": str(exc)},
    )
    if isinstance(exc, UnsafeUrlError):
        # The caller supplied the URL — a refusal to fetch it is a client
        # error, not an upstream failure.
        raise StacFetchError(400, f"URL rejected: {exc}") from exc
    if isinstance(exc, httpx.HTTPStatusError):
        raise StacFetchError(
            exc.response.status_code,
            f"Failed to fetch root STAC object from {root_url}: "
            f"{exc.response.status_code}",
        ) from exc
    raise StacFetchError(
        None, f"Error retrieving root STAC object from {root_url}: {exc}"
    ) from exc


# ─── Aggregation ─────────────────────────────────────────────────────────────


def flatten_results(
    node: ValidationTreeNode,
) -> tuple[int, list[ValidationIssue], list[ValidationIssue]]:
    """Flatten the tree into total count, errors list, and warnings list."""
    total = 1
    errors = [i for i in node.issues if i.level == ValidationLevel.ERROR]
    warnings = [i for i in node.issues if i.level == ValidationLevel.WARNING]

    for child in node.children:
        c_total, c_errs, c_warns = flatten_results(child)
        total += c_total
        errors.extend(c_errs)
        warnings.extend(c_warns)

    return total, errors, warnings
