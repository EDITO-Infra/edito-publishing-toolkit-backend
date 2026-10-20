"""Public models for the STAC find feature.

Find is a passthrough scan of the EDITO STAC API: upstream payloads are
wrapped as-is (``extra="allow"``), never re-shaped field by field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "FindCatalogsResponse",
    "FindCollectionsResponse",
    "FindItemsResponse",
    "FindPropertiesResponse",
]


class FindItemsResponse(BaseModel):
    """STAC Item Search result (upstream FeatureCollection, passthrough).

    ``number_matched`` is upstream's ``numberMatched`` — meaningful for
    ``/items`` and ``/items/scientific``; the ``/assets`` client-side scan
    leaves it ``None`` since the upstream count describes the unfiltered
    scan, not the matches.
    """

    type: str = "FeatureCollection"
    features: list[dict[str, Any]] = Field(default_factory=list)
    number_matched: int | None = None
    number_returned: int | None = None

    model_config = ConfigDict(extra="allow")


class FindCollectionsResponse(BaseModel):
    """Collections matching the query (upstream collection dicts, passthrough)."""

    collections: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class FindCatalogsResponse(BaseModel):
    """Child catalogs found in the catalog tree (upstream link dicts, passthrough)."""

    catalogs: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class FindPropertiesResponse(BaseModel):
    """Items whose properties match all key=value pairs (upstream items, passthrough)."""

    features: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")
