"""STAC Item construction primitives.

Pure STAC-shaped input/serialization: no ISO19139, CSW, file, S3, or API
knowledge. Features map their domain data onto ``StacItemInput`` and call
``build_item`` / ``validate_item``. Asset/geometry decisions (media types,
which assets to include, bbox → polygon) belong to the feature adapters.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Literal

import pystac
from pydantic import BaseModel, Field, model_validator
from stac_validator.fast_validator import FastValidator

logger = logging.getLogger(__name__)

__all__ = [
    "StacAssetInput",
    "StacItemInput",
    "StacLinkInput",
    "bbox_to_geometry",
    "build_item",
    "geometry_bbox",
    "validate_item",
]


class StacAssetInput(BaseModel):
    """Normalized STAC asset input before serialization."""

    href: str
    title: str | None = None
    description: str | None = None
    type: str | None = None
    roles: list[str] = Field(default_factory=list)
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class StacLinkInput(BaseModel):
    """Normalized STAC link input before serialization."""

    href: str
    rel: str
    type: str | None = None
    title: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class StacItemInput(BaseModel):
    """Strict-ish STAC Item input model expected by ``build_item``."""

    id: str
    geometry: dict[str, Any]
    bbox: list[float]
    properties: dict[str, Any] = Field(default_factory=dict)

    type: Literal["Feature"] = "Feature"
    stac_version: str = "1.1.0"
    stac_extensions: list[str] = Field(default_factory=list)
    links: list[StacLinkInput] = Field(default_factory=list)
    assets: dict[str, StacAssetInput] = Field(default_factory=dict)

    collection: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_stac_temporal_information(self) -> StacItemInput:
        props = self.properties or {}

        has_datetime = props.get("datetime") is not None
        has_interval = (
            props.get("start_datetime") is not None
            and props.get("end_datetime") is not None
        )

        if not has_datetime and not has_interval:
            raise ValueError(
                "STAC Item input requires either properties.datetime "
                "or both properties.start_datetime and properties.end_datetime."
            )

        if len(self.bbox) not in (4, 6):
            raise ValueError("STAC bbox must contain 4 or 6 coordinates.")

        return self


def build_item(item: StacItemInput) -> dict[str, Any]:
    """Build a STAC Item dict from strict STAC-shaped input."""

    payload: dict[str, Any] = {
        "type": item.type,
        "stac_version": item.stac_version,
        "id": item.id,
        "geometry": item.geometry,
        "bbox": item.bbox,
        "properties": item.properties,
        "links": [_link_to_dict(link) for link in item.links],
        "assets": {
            key: _asset_to_dict(asset) for key, asset in item.assets.items()
        },
    }

    if item.stac_extensions:
        payload["stac_extensions"] = item.stac_extensions

    if item.collection:
        payload["collection"] = item.collection

    payload.update(item.extra_fields)
    logger.info(
        "Built STAC Item id=%s assets=%d links=%d",
        item.id,
        len(item.assets),
        len(item.links),
    )
    return payload


def validate_item(item: dict[str, Any]) -> None:
    """Schema-validate a STAC Item dict (stac-validator fast path); raise ValueError on failure.

    fastjsonschema against the official core/extension schemas — links are
    checked as data (e.g. rel=collection presence), never resolved.
    """

    validator = FastValidator(stac_file="in-memory", quiet=True)
    # run_dict enriches nested link dicts in place (e.g. method: GET) on a
    # shallow copy — deepcopy so the caller's object stays untouched.
    validation_copy = copy.deepcopy(item)
    # stac-valid's fast-path schema optimizer collapses the properties oneOf,
    # so interval-only items (start_datetime + end_datetime, no datetime) are
    # falsely rejected. Inject a derived datetime into the throwaway copy;
    # only when both bounds exist, so genuinely incomplete intervals still fail.
    # honey: revisit when stac-valid >4.6.1 fixes the optimizer
    props = validation_copy.get("properties")
    if (
        isinstance(props, dict)
        and "datetime" not in props
        and "start_datetime" in props
        and "end_datetime" in props
    ):
        props["datetime"] = props["start_datetime"]
    validator.run_dict(validation_copy)
    if not validator.valid:
        errors = [
            entry["error_message"]
            for report in validator.message
            for entry in report.get("errors", [])
        ]
        raise ValueError(f"Generated STAC Item failed schema validation: {'; '.join(errors) or 'unknown'}")
    # STAC geometry/bbox are always WGS84 — the JSON schema doesn't constrain
    # coordinate values, so bounds are checked explicitly here.
    geom_bbox = geometry_bbox(item.get("geometry"))
    if geom_bbox and (
        geom_bbox[0] < -180
        or geom_bbox[1] < -90
        or geom_bbox[2] > 180
        or geom_bbox[3] > 90
    ):
        raise ValueError(
            "Generated STAC Item failed schema validation: "
            "geometry coordinates outside WGS84 lon/lat bounds"
        )
    logger.info("Validated STAC Item id=%s", item.get("id"))


def bbox_to_geometry(
    bbox: list[float] | None,
) -> tuple[dict[str, Any] | None, list[float] | None]:
    """Convert a 2D (4-value) or 3D (6-value) bbox into a polygon geometry and bbox."""

    if not bbox or len(bbox) not in (4, 6):
        logger.debug("Cannot convert bbox to geometry bbox=%s", bbox)
        return None, None

    west, south, east, north = (float(value) for value in (bbox[0], bbox[1], bbox[-2], bbox[-1]))

    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }

    return geometry, [float(value) for value in bbox]


def geometry_bbox(geometry: dict[str, Any] | None) -> list[float] | None:
    """Bbox of a GeoJSON geometry via a flat coordinate scan; None when empty."""

    if not isinstance(geometry, dict):
        return None

    def scan(value: Any) -> list[float]:
        if (
            isinstance(value, (list, tuple))
            and len(value) >= 2
            and all(isinstance(v, (int, float)) for v in value[:2])
        ):
            return [float(value[0]), float(value[1])]
        if not isinstance(value, (list, tuple)):
            return []
        coords: list[float] = []
        for item in value:
            point = scan(item)
            if point:
                if not coords:
                    coords = point[:]
                else:
                    coords[0] = min(coords[0], point[0])
                    coords[1] = min(coords[1], point[1])
                    coords.extend(point)
        return coords

    flat = scan(geometry.get("coordinates"))
    if not flat:
        return None
    xs, ys = flat[0::2], flat[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


def _asset_to_dict(asset: StacAssetInput) -> dict[str, Any]:
    """Serialize a STAC asset model while preserving extra fields."""

    data = asset.model_dump(exclude_none=True, exclude={"extra_fields"})
    data.update(asset.extra_fields)
    return data


def _link_to_dict(link: StacLinkInput) -> dict[str, Any]:
    """Serialize a STAC link model while preserving extra fields."""

    data = link.model_dump(exclude_none=True, exclude={"extra_fields"})
    data.update(link.extra_fields)
    return data
