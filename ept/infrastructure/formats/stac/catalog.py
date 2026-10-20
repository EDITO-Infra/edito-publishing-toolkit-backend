"""STAC Catalog construction primitives.

Pure STAC-shaped construction/validation, mirroring ``item.py``: no file, S3,
or API knowledge. Features map their request fields onto ``CatalogInput`` and
call ``build_catalog`` / ``validate_catalog``; overlay and link policy stay
with the feature.
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
    "CatalogInput",
    "build_catalog",
    "validate_catalog",
]


class CatalogInput(BaseModel):
    """Strict STAC Catalog input expected by ``build_catalog``."""

    id: str
    title: str | None = None
    description: str | None = None
    stac_extensions: list[str] = Field(default_factory=list)


def build_catalog(catalog: CatalogInput) -> dict[str, Any]:
    """Build a STAC Catalog dict from strict STAC-shaped input."""

    obj = pystac.Catalog(
        id=catalog.id,
        description=catalog.description or catalog.title or catalog.id,
        title=catalog.title,
        stac_extensions=catalog.stac_extensions,
    )
    stac_object = obj.to_dict(include_self_link=False, transform_hrefs=False)
    stac_object["stac_version"] = "1.1.0"
    return stac_object


def validate_catalog(catalog: dict[str, Any]) -> None:
    """Schema-validate a STAC Catalog dict (stac-validator fast path); raise ValueError on failure.

    fastjsonschema against the official core schemas — links are checked as
    data (rel/shape), never resolved.
    """

    validator = FastValidator(stac_file="in-memory", quiet=True)
    # run_dict enriches nested link dicts in place (e.g. method: GET) on a
    # shallow copy — deepcopy so the caller's object stays untouched.
    validator.run_dict(copy.deepcopy(catalog))
    if not validator.valid:
        errors = [
            entry["error_message"]
            for report in validator.message
            for entry in report.get("errors", [])
        ]
        raise ValueError(f"Generated STAC Catalog failed schema validation: {'; '.join(errors) or 'unknown'}")
    logger.info("Validated STAC Catalog id=%s", catalog.get("id"))
