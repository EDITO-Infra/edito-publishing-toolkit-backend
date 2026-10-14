"""EDITO policy enforcement + schema validation glue.

Schema validation (``StacSchemaValidator``) is feature-local (``validation.py``);
this module maps results into feature-level ``ValidationIssue`` models. EDITO
publishing rules are split into per-object-type functions so adding or
removing a rule touches one function.

All functions here operate on raw STAC JSON dicts (no pystac) and are
guarded — a crash in schema validation or EDITO rule checking is captured
as an ERROR-level ``ValidationIssue`` on the node, never raised. This
supports ``continue_on_error`` in the service layer: no single object's
failure aborts the validation run.
"""

from __future__ import annotations

from urllib.parse import urlsplit
from typing import List, Optional
import logging
import time

from ept.infrastructure.formats.stac import (
    CollectedObject,
    StacObjectType,
    get_stac_object_type,
)

from .validation import StacSchemaValidator


from .models import ValidationIssue, ValidationLevel, ValidationTreeNode

logger = logging.getLogger(__name__)

# --- Error messages ---------------------------------------------------------

ITEM_NOT_IN_COLLECTION_ERROR = (
    "EDITO items must be children of a collection; catalogs must not link "
    "directly to items"
)
COLLECTION_IN_COLLECTION_ERROR = (
    "EDITO does not currently support collections nested inside collections"
)
ASSET_HREF_ERROR = "EDITO item asset hrefs must be public http(s):// URLs"


# ─── Schema validation glue ────────────────────────────────────────────────


def run_schema_validation(
    validator: StacSchemaValidator,
    data: dict,
    location: Optional[str] = None,
) -> List[ValidationIssue]:
    """Run STAC schema validation using a reused ``FastValidator`` instance.

    The caller should create **one** ``StacSchemaValidator`` and pass it to
    every call so the compiled-validator cache stays warm.

    ``location`` is the canonical URL of the object in the traversal (the
    caller's ``CollectedObject.url``).

    Never raises — ``StacSchemaValidator.validate`` catches all crashes
    internally and returns them as error strings.
    """
    errors = validator.validate(data, source=location or "in-memory")
    return [_schema_issue(data, msg, location) for msg in errors]


def _schema_issue(
    data: dict,
    message: str,
    location: Optional[str] = None,
) -> ValidationIssue:
    return ValidationIssue(
        level=ValidationLevel.ERROR,
        object_type=get_stac_object_type(data),
        object_id=data.get("id"),
        location=location or "unknown",
        rule="stac-core-schema",
        message=message,
    )


# ─── EDITO policy: dispatcher ──────────────────────────────────────────────


def validate_edito_constraints(
    data: dict,
    parent_type: Optional[StacObjectType] = None,
    location: Optional[str] = None,
) -> List[ValidationIssue]:
    """Run EDITO publishing rules for one object in its traversal context.

    ``location`` is the canonical traversal URL (the caller's
    ``CollectedObject.url``).

    Dispatches to a per-type handler. To add a rule for a specific object
    type, edit the corresponding ``_validate_edito_*`` function.

    Never raises — crashes are returned as ``ValidationIssue`` entries with
    rule ``edito-internal-error``.
    """
    kind = get_stac_object_type(data)

    try:
        if kind is StacObjectType.ITEM:
            issues = _validate_edito_item(data, parent_type, location)
        elif kind is StacObjectType.COLLECTION:
            issues = _validate_edito_collection(data, parent_type, location)
        else:
            issues = []
    except Exception as exc:
        logger.error(
            "EDITO constraint validation crashed",
            extra={
                "source": location or "in-memory",
                "obj_id": data.get("id"),
                "obj_type": kind.value,
                "error": str(exc),
            },
        )
        return [
            ValidationIssue(
                level=ValidationLevel.ERROR,
                object_type=kind,
                object_id=data.get("id"),
                location=location or "unknown",
                rule="edito-internal-error",
                message=f"EDITO constraint validation crashed: {exc}",
            )
        ]

    if not issues:
        logger.debug(
            "EDITO constraints OK",
            extra={
                "source": location or "in-memory",
                "obj_id": data.get("id"),
                "obj_type": kind.value,
            },
        )
    else:
        logger.debug(
            "EDITO constraint violations found",
            extra={
                "source": location or "in-memory",
                "obj_id": data.get("id"),
                "obj_type": kind.value,
                "violation_count": len(issues),
                "rules": [i.rule for i in issues],
            },
        )

    return issues


# ─── EDITO policy: per-object-type rules ──────────────────────────────────


def _validate_edito_item(
    data: dict,
    parent_type: Optional[StacObjectType],
    location: Optional[str] = None,
) -> List[ValidationIssue]:
    """EDITO rules for Item objects.

    - Items must be children of a Collection (not a Catalog, not root).
    - Every asset href must be a public ``http(s)://`` URL.
    """
    issues: List[ValidationIssue] = []

    # Rule: items must be children of a Collection
    if parent_type is not StacObjectType.COLLECTION:
        issues.append(
            _issue(data, "edito_item_not_in_collection", ITEM_NOT_IN_COLLECTION_ERROR, location)
        )

    # Rule: asset hrefs must be public http(s)://
    issues.extend(_validate_edito_assets(data, location))

    return issues


def _validate_edito_collection(
    data: dict,
    parent_type: Optional[StacObjectType],
    location: Optional[str] = None,
) -> List[ValidationIssue]:
    """EDITO rules for Collection objects.

    - Collections must not be nested inside other Collections.
    """
    issues: List[ValidationIssue] = []

    if parent_type is StacObjectType.COLLECTION:
        issues.append(
            _issue(data, "edito_collection_in_collection", COLLECTION_IN_COLLECTION_ERROR, location)
        )

    return issues


# ─── EDITO policy: asset href validation ──────────────────────────────────


def _validate_edito_assets(data: dict, location: Optional[str] = None) -> List[ValidationIssue]:
    """Require every item asset href to be a public ``https://`` URL."""
    issues: List[ValidationIssue] = []

    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        return issues

    for asset_key, asset in assets.items():
        href = asset.get("href") if isinstance(asset, dict) else None
        issues.extend(
            _validate_edito_asset_href(
                href=href,
                obj_id=data.get("id"),
                asset_key=asset_key,
                ref=location or "unknown",
            )
        )
    return issues


def _validate_edito_asset_href(
    href: Optional[str],
    *,
    obj_id: Optional[str],
    asset_key: str,
    ref: str,
) -> List[ValidationIssue]:
    """Check one asset entry's href against the public ``https://`` rule."""

    def _fail() -> ValidationIssue:
        shown = repr(href) if href and href.strip() else "(missing)"
        return ValidationIssue(
            level=ValidationLevel.ERROR,
            object_type=StacObjectType.ITEM,
            object_id=obj_id,
            location=ref,
            rule="edito_asset_href_not_public_https",
            message=f"{ASSET_HREF_ERROR} [asset '{asset_key}': {shown}]",
        )

    if href is None or not isinstance(href, str) or not href.strip():
        return [_fail()]

    try:
        parsed = urlsplit(href.strip())
        host = parsed.hostname
    except ValueError:
        parsed = None
        host = None

    # Scheme must be http(s), host must exist, no embedded credentials
    if (
        parsed is None
        or parsed.scheme.lower() not in ["https", "http"]
        or not host
        or parsed.username is not None
        or parsed.password is not None
    ):
        return [_fail()]

    return []


# ─── Helpers ────────────────────────────────────────────────────────────


def _issue(
    data: dict,
    rule: str,
    message: str,
    location: Optional[str] = None,
) -> ValidationIssue:
    """Build an ERROR-level ``ValidationIssue`` with standard fields."""
    return ValidationIssue(
        level=ValidationLevel.ERROR,
        object_type=get_stac_object_type(data),
        object_id=data.get("id"),
        location=location or "unknown",
        rule=rule,
        message=message,
    )


# ─── Batch adaptation: CollectedObjects → feature tree nodes ───────────────


def _make_error_node(url: str, rule: str, message: str) -> ValidationTreeNode:
    """Create an error node for an object that failed to fetch or parse."""
    return ValidationTreeNode(
        url=url,
        object_type=StacObjectType.UNKNOWN,
        object_id=None,
        object_is_valid=False,
        issues=[
            ValidationIssue(
                level=ValidationLevel.ERROR,
                object_type=StacObjectType.UNKNOWN,
                object_id=None,
                location=url,
                rule=rule,
                message=message,
            )
        ],
        children=[],
    )


def validate_batch(
    batch: list[CollectedObject],
    schema_validator: StacSchemaValidator,
) -> dict[str, ValidationTreeNode]:
    """Validate one batch of collected objects into feature tree nodes.

    Called via ``asyncio.to_thread`` by the service so it doesn't block the
    event loop while the next batch's HTTP requests are in flight.

    **Failure ordering is natural:**
      - Fetch errors → the object has ``fetch_error`` set; create an error
        node and skip schema + EDITO validation.
      - Schema validation → runs first; fails fast if the object isn't
        valid STAC.
      - EDITO policy → runs second; only meaningful if the object is
        structurally valid STAC.

    **Failure behaviour:** neither validator raises (one guard each, see
    :func:`run_schema_validation` / :func:`validate_edito_constraints`), so
    this always returns a node for every object and never raises.

    Returns a ``dict[url, ValidationTreeNode]`` so the service can assemble
    the tree across batches.
    """
    nodes: dict[str, ValidationTreeNode] = {}

    for item in batch:
        # ── Fetch error → error node, skip validation ─────────────────
        if item.fetch_error:
            logger.warning(
                "FETCH FAILED: %s depth=%d → %s",
                item.url, item.depth, item.fetch_error,
            )
            nodes[item.url] = _make_error_node(
                item.url, "fetch-error",
                f"Failed to fetch STAC object: {item.fetch_error}",
            )
            continue

        # ── Validate ─────────────────────────────────────────
        data = item.data or {}
        issues: list[ValidationIssue] = []
        schema_seconds = 0.0
        edito_seconds = 0.0

        t_schema = time.perf_counter()
        issues.extend(
            run_schema_validation(schema_validator, data, location=item.url)
        )
        schema_seconds = time.perf_counter() - t_schema

        t_edito = time.perf_counter()
        issues.extend(
            validate_edito_constraints(
                data, parent_type=item.parent_type, location=item.url
            )
        )
        edito_seconds = time.perf_counter() - t_edito

        # ── Build node ───────────────────────────────────────────────
        is_valid = not any(
            i.level == ValidationLevel.ERROR for i in issues
        )

        node = ValidationTreeNode(
            url=item.url,
            object_type=item.obj_type,
            object_id=data.get("id"),
            object_is_valid=is_valid,
            issues=issues,
            children=[],
        )
        nodes[item.url] = node

        # ── Per-object log ───────────────────────────────────────
        # One self-contained line per object: the INFO level shows the
        # validate outcome for every object at default LOG_LEVEL; DEBUG
        # additionally shows the per-fetch/RESOLVED wire steps.
        if is_valid:
            logger.debug(
                "VALIDATE OK: %s [%s] id=%r depth=%d schema=%.4fs edito=%.4fs issues=%d",
                item.url,
                item.obj_type.value,
                data.get("id"),
                item.depth,
                schema_seconds,
                edito_seconds,
                len(issues),
            )
        else:
            logger.warning(
                "VALIDATE FAILED: %s [%s] id=%r depth=%d schema=%.4fs edito=%.4fs rules=%s",
                item.url,
                item.obj_type.value,
                data.get("id"),
                item.depth,
                schema_seconds,
                edito_seconds,
                [i.rule for i in issues],
            )

    return nodes
