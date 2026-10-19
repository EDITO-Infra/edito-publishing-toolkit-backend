"""STAC Collection construction primitives.

Pure STAC-shaped construction/validation, mirroring ``item.py``: no file, S3,
or API knowledge. Features map their request fields onto ``CollectionInput``
and call ``build_collection`` / ``validate_collection``; overlay and link
policy stay with the feature.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

import pystac
from pydantic import BaseModel, Field
from stac_validator.fast_validator import FastValidator

logger = logging.getLogger(__name__)

__all__ = [
    "CollectionInput",
    "build_collection",
    "validate_collection",
]


class CollectionInput(BaseModel):
    """Strict STAC Collection input expected by ``build_collection``."""

    id: str
    title: str | None = None
    description: str | None = None
    stac_extensions: list[str] = Field(default_factory=list)
    extent: dict[str, Any] | None = None
    license: str | None = None
    providers: list[dict[str, Any]] = Field(default_factory=list)


def build_collection(collection: CollectionInput) -> dict[str, Any]:
    """Build a STAC Collection dict from strict STAC-shaped input."""

    try:
        obj = pystac.Collection(
            id=collection.id,
            description=collection.description or collection.title or collection.id,
            title=collection.title,
            stac_extensions=collection.stac_extensions,
            extent=(
                pystac.Extent.from_dict(collection.extent)
                if collection.extent
                # pystac crashes serializing a Collection without an extent;
                # default to a world-wide open interval and warn instead.
                else pystac.Extent(
                    # Schema-valid world bbox: an empty SpatialExtent([[]]) would
                    # fail the official collection schema (bbox needs numbers).
                    spatial=pystac.SpatialExtent([[-180.0, -90.0, 180.0, 90.0]]),
                    temporal=pystac.TemporalExtent([[None, None]]),
                )
            ),
            license=collection.license or "proprietary",
            providers=[pystac.Provider.from_dict(provider) for provider in collection.providers],
        )
    except (pystac.STACError, KeyError, TypeError) as exc:
        raise ValueError(f"Cannot build STAC Collection: {exc}") from exc

    stac_object = obj.to_dict(include_self_link=False, transform_hrefs=False)
    stac_object["stac_version"] = "1.1.0"
    return stac_object


def validate_collection(collection: dict[str, Any]) -> None:
    """Schema-validate a STAC Collection dict (stac-validator fast path); raise ValueError on failure.

    fastjsonschema against the official core schemas — links are checked as
    data (rel/shape), never resolved.
    """

    validator = FastValidator(stac_file="in-memory", quiet=True)
    # run_dict enriches nested link dicts in place (e.g. method: GET) on a
    # shallow copy — deepcopy so the caller's object stays untouched.
    validator.run_dict(copy.deepcopy(collection))
    if not validator.valid:
        errors = [
            entry["error_message"]
            for report in validator.message
            for entry in report.get("errors", [])
        ]
        raise ValueError(f"Generated STAC Collection failed schema validation: {'; '.join(errors) or 'unknown'}")
    logger.info("Validated STAC Collection id=%s", collection.get("id"))
