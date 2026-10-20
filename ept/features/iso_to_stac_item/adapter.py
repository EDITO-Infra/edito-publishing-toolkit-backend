from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, Field

from ept.infrastructure.formats.iso19139 import parse_isoxml
from ept.infrastructure.formats.stac import (
    StacAssetInput,
    StacItemInput,
    StacLinkInput,
    bbox_to_geometry,
)
from ept.infrastructure.formats.stac.licenses import match_spdx_ids
from ept.infrastructure.services.editostacapi import collection_href

logger = logging.getLogger(__name__)


class Iso19139ToStacOptions(BaseModel):
    """
    Options for the shared ISO19139 -> STAC Item input primitive.

    This model is shared by features that need the same ISO-to-STAC mapping,
    such as:
      - iso_to_stac_item
      - future csw_to_stac
    """

    iso_schema: str | None = "http://www.isotc211.org/2005/gmd"
    csw_endpoint: str | None = None
    csw_version: str | None = None
    assets_config_path: str | None = None
    default_collection: str | None = None
    stac_version: str = "1.1.0"

    include_distribution_assets: bool = True
    include_csw_metadata_asset: bool = True
    include_keywords: bool = True
    include_responsible_parties: bool = True
    include_constraints: bool = True
    include_licenses: bool = True

    extra_properties: dict[str, Any] = Field(default_factory=dict)


class ExtractedIso19139Metadata(BaseModel):
    """
    Normalized ISO-derived metadata.

    This is not the final STAC Item model. It is the extracted ISO content
    before STAC mapping.
    """

    identifier: str | None = None
    title: str | None = None
    abstract: str | None = None
    bbox: list[float] | None = None
    temporal: dict[str, Any] = Field(default_factory=dict)
    keywords: list[str] = Field(default_factory=list)
    responsible_parties: list[dict[str, Any]] = Field(default_factory=list)
    online_resources: list[dict[str, Any]] = Field(default_factory=list)
    resource_constraints: list[dict[str, Any]] = Field(default_factory=list)
    licenses: list[dict[str, Any]] = Field(default_factory=list)


def iso19139_to_stac_item_input(
    xml: str,
    *,
    options: Iso19139ToStacOptions | None = None,
    record_id: str | None = None,
) -> StacItemInput:
    """
    Shared primitive: ISO19139 XML -> strict STAC Item input.
    """

    transformer = Iso19139ToStacTransformer(options or Iso19139ToStacOptions())
    return transformer.transform(xml, record_id=record_id)


class Iso19139ToStacTransformer:
    def __init__(self, options: Iso19139ToStacOptions) -> None:
        self.options = options
        self.asset_type_config = _load_asset_config(options.assets_config_path)

    def transform(self, xml: str, *, record_id: str | None = None) -> StacItemInput:
        extractor = Iso19139Extractor(xml)
        extracted = extractor.extract()

        item_id = record_id or extracted.identifier or _safe_id(extracted.title) or "unknown-id"

        geometry, bbox = bbox_to_geometry(extracted.bbox)
        if geometry is None or bbox is None:
            raise ValueError("ISO19139 metadata does not contain a valid geographic bounding box.")

        temporal_properties = self._map_temporal(extracted.temporal)
        assets = self._map_assets(extracted, record_id=item_id)
        properties = self._map_properties(extracted, temporal_properties)

        return StacItemInput(
            id=item_id,
            geometry=geometry,
            bbox=bbox,
            properties=properties,
            collection=self.options.default_collection,
            stac_version=self.options.stac_version,
            links=_collection_link(self.options.default_collection),
            assets=assets,
        )

    def _map_temporal(self, temporal: dict[str, Any]) -> dict[str, Any]:
        additional_dates = _dedupe_additional_dates(temporal.get("additional_dates", []) or [])
        chosen = _pick_preferred_datetime(additional_dates)

        start = temporal.get("start")
        end = temporal.get("end")

        properties: dict[str, Any] = {}

        if start and end:
            properties["datetime"] = None
            properties["start_datetime"] = start
            properties["end_datetime"] = end
        elif chosen:
            properties["datetime"] = chosen["value"]
            properties["datetime_source"] = chosen["type"]
        else:
            raise ValueError("No valid temporal information found in ISO metadata.")

        if additional_dates:
            properties["additional_dates"] = additional_dates

        return properties

    def _map_assets(
        self,
        extracted: ExtractedIso19139Metadata,
        *,
        record_id: str | None,
    ) -> dict[str, StacAssetInput]:
        assets: dict[str, StacAssetInput] = {}

        if self.options.include_distribution_assets:
            for idx, resource in enumerate(extracted.online_resources):
                asset_name, asset = self._online_resource_to_asset(resource, idx)
                if asset:
                    assets[asset_name] = asset

        if self.options.include_csw_metadata_asset:
            href = self._build_csw_getrecordbyid_href(record_id)
            if href:
                assets["csw_iso_metadata"] = StacAssetInput(
                    href=href,
                    title="CSW ISO metadata",
                    description="Original ISO metadata record via CSW GetRecordById.",
                    type="application/xml",
                    roles=["metadata"],
                )

        return assets

    def _online_resource_to_asset(
        self,
        resource: dict[str, Any],
        idx: int,
    ) -> tuple[str, StacAssetInput | None]:
        href = resource.get("url")

        if not isinstance(href, str) or not href.strip():
            return f"asset-{idx}", None

        href = href.strip()
        name = resource.get("name")
        description = resource.get("description")
        protocol = resource.get("protocol")

        configured = _match_asset_config(
            href,
            self.asset_type_config,
        )

        if configured:
            asset_name = configured.get("asset_type") or _safe_id(name) or f"asset-{idx}"
            return asset_name, StacAssetInput(
                href=href,
                title=configured.get("title") or name or asset_name,
                description=description,
                type=configured.get("mediatype") or configured.get("media_type"),
                roles=configured.get("roles") or [],
            )

        role = None
        if isinstance(protocol, str) and protocol.lower() in {"wms", "wmts", "wfs"}:
            role = "data"

        asset_name = _safe_id(name) or f"asset-{idx}"

        return asset_name, StacAssetInput(
            href=href,
            title=name or asset_name,
            description=description,
            roles=[role] if role else [],
        )

    def _map_properties(
        self,
        extracted: ExtractedIso19139Metadata,
        temporal_properties: dict[str, Any],
    ) -> dict[str, Any]:
        properties: dict[str, Any] = {}

        properties.update(temporal_properties)

        if extracted.title:
            properties["title"] = re.sub(r"[_\n]+", " ", extracted.title).strip()

        if extracted.abstract:
            properties["description"] = extracted.abstract.strip()
            properties["abstract"] = extracted.abstract.strip()

        if self.options.include_keywords and extracted.keywords:
            properties["keywords"] = extracted.keywords

        if self.options.include_responsible_parties and extracted.responsible_parties:
            properties["providers"] = extracted.responsible_parties

        if self.options.include_constraints and extracted.resource_constraints:
            properties["constraints"] = extracted.resource_constraints

        if self.options.include_licenses and extracted.licenses:
            properties["licenses"] = extracted.licenses

        if self.options.csw_endpoint:
            properties["csw_endpoint"] = self.options.csw_endpoint

        properties.update(self.options.extra_properties)

        return properties

    def _build_csw_getrecordbyid_href(self, record_id: str | None) -> str | None:
        if not self.options.csw_endpoint:
            return None

        endpoint = self.options.csw_endpoint.strip()
        if not endpoint:
            return None

        if not record_id:
            return endpoint

        parts = urlsplit(endpoint)
        existing = dict(parse_qsl(parts.query, keep_blank_values=True))

        params = {
            **existing,
            "service": "CSW",
            "version": self.options.csw_version,
            "request": "GetRecordById",
            "elementSetName": "full",
            "outputSchema": self.options.iso_schema,
            "id": record_id.strip(),
        }

        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(params, doseq=True),
                parts.fragment,
            )
        )


class Iso19139Extractor:
    """
    Extract ISO19139 fields needed for STAC-oriented transformation.

    Parsing is delegated to the ``ept.infrastructure.formats.iso19139``
    primitives; this class owns field extraction and normalization.
    """

    def __init__(self, xml: str) -> None:
        self.xml = xml
        self.root, self.ns = parse_isoxml(xml)

    def extract(self) -> ExtractedIso19139Metadata:
        return ExtractedIso19139Metadata(
            identifier=self._identifier(),
            title=self._title(),
            abstract=self._abstract(),
            bbox=self._bbox(),
            temporal=self._temporal(),
            keywords=self._keywords(),
            responsible_parties=self._responsible_parties(),
            online_resources=self._online_resources(),
            resource_constraints=self._resource_constraints(),
            licenses=self._licenses(),
        )

    def _first(self, xpath: str, node: Any = None) -> Any:
        return (node if node is not None else self.root).find(xpath, self.ns)

    def _all(self, xpath: str, node: Any = None) -> list:
        return list((node if node is not None else self.root).findall(xpath, self.ns))

    def _txt(self, element: Any) -> str | None:
        if element is None or element.text is None:
            return None
        value = element.text.strip()
        return value or None

    def _identifier(self) -> str | None:
        return self._txt(self._first("gmd:fileIdentifier/gco:CharacterString"))

    def _title(self) -> str | None:
        return self._txt(
            self._first(
                ".//gmd:identificationInfo//gmd:citation//gmd:title//gco:CharacterString"
            )
        )

    def _abstract(self) -> str | None:
        return self._txt(
            self._first(".//gmd:identificationInfo//gmd:abstract//gco:CharacterString")
        )

    def _bbox(self) -> list[float] | None:
        west = self._txt(
            self._first(".//gmd:EX_GeographicBoundingBox//gmd:westBoundLongitude//gco:Decimal")
        )
        south = self._txt(
            self._first(".//gmd:EX_GeographicBoundingBox//gmd:southBoundLatitude//gco:Decimal")
        )
        east = self._txt(
            self._first(".//gmd:EX_GeographicBoundingBox//gmd:eastBoundLongitude//gco:Decimal")
        )
        north = self._txt(
            self._first(".//gmd:EX_GeographicBoundingBox//gmd:northBoundLatitude//gco:Decimal")
        )

        try:
            if not (west and south and east and north):
                return None
            return [float(west), float(south), float(east), float(north)]
        except ValueError:
            return None

    def _keywords(self) -> list[str]:
        keywords: list[str] = []

        for node in self._all(
            ".//gmd:descriptiveKeywords//gmd:MD_Keywords//gmd:keyword//gco:CharacterString"
        ):
            value = self._txt(node)
            if value:
                keywords.append(value)

        return sorted(set(keywords))

    def _temporal(self) -> dict[str, Any]:
        begin = (
            self._txt(
                self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml:TimePeriod/gml:beginPosition")
            )
            or self._txt(
                self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml32:TimePeriod/gml32:beginPosition")
            )
            or self._txt(self._first(".//gml:TimePeriod/gml:beginPosition"))
            or self._txt(self._first(".//gml32:TimePeriod/gml32:beginPosition"))
        )

        end = (
            self._txt(
                self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml:TimePeriod/gml:endPosition")
            )
            or self._txt(
                self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml32:TimePeriod/gml32:endPosition")
            )
            or self._txt(self._first(".//gml:TimePeriod/gml:endPosition"))
            or self._txt(self._first(".//gml32:TimePeriod/gml32:endPosition"))
        )

        start = _normalize_datetime(begin)
        end_dt = _normalize_datetime(end)

        if not start and not end_dt:
            instant = (
                self._txt(
                    self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml:TimeInstant/gml:timePosition")
                )
                or self._txt(
                    self._first(".//gmd:EX_TemporalExtent//gmd:extent//gml32:TimeInstant/gml32:timePosition")
                )
            )
            instant_dt = _normalize_datetime(instant)
            if instant_dt:
                start = instant_dt
                end_dt = instant_dt

        additional_dates: list[dict[str, str]] = []

        for ci_date in self._all(".//gmd:CI_Date"):
            date_type = self._ci_date_type(ci_date)
            raw_value = (
                self._txt(self._first("gmd:date/gco:DateTime", ci_date))
                or self._txt(self._first("gmd:date/gco:Date", ci_date))
            )
            value = _normalize_datetime(raw_value)
            if date_type and value:
                additional_dates.append({"type": date_type, "value": value})

        stamp = (
            self._txt(self._first(".//gmd:dateStamp//gco:DateTime"))
            or self._txt(self._first(".//gmd:dateStamp//gco:Date"))
        )
        stamp_value = _normalize_datetime(stamp)
        if stamp_value:
            additional_dates.append({"type": "datestamp", "value": stamp_value})

        additional_dates = _dedupe_additional_dates(additional_dates)

        return {
            "start": start,
            "end": end_dt,
            "additional_dates": additional_dates,
        }

    def _ci_date_type(self, ci_date: Any) -> str | None:
        node = self._first("gmd:dateType/gmd:CI_DateTypeCode", ci_date)
        value = self._txt(node)

        if not value and node is not None:
            value = node.attrib.get("codeListValue")

        return value.strip().lower() if isinstance(value, str) and value.strip() else None

    def _responsible_parties(self) -> list[dict[str, Any]]:
        parties: list[dict[str, Any]] = []

        for rp in self._all(".//gmd:CI_ResponsibleParty"):
            org = self._txt(self._first(".//gmd:organisationName//gco:CharacterString", rp))
            individual = self._txt(self._first(".//gmd:individualName//gco:CharacterString", rp))
            role = self._txt(self._first(".//gmd:role//gmd:CI_RoleCode", rp))

            name = org or individual
            if not name:
                continue

            parties.append(
                {
                    "name": name,
                    "roles": [role.lower()] if role else [],
                }
            )

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []

        for party in parties:
            key = party["name"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(party)

        return unique

    def _online_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []

        for node in self._all(
            ".//gmd:distributionInfo//gmd:transferOptions//gmd:onLine//gmd:CI_OnlineResource"
        ):
            url = self._txt(self._first(".//gmd:linkage//gmd:URL", node))
            if not url:
                continue

            resources.append(
                {
                    "url": url,
                    "name": self._txt(self._first(".//gmd:name//gco:CharacterString", node)),
                    "description": self._txt(
                        self._first(".//gmd:description//gco:CharacterString", node)
                    ),
                    "protocol": self._txt(
                        self._first(".//gmd:protocol//gco:CharacterString", node)
                    ),
                }
            )

        return resources

    def _resource_constraints(self) -> list[dict[str, Any]]:
        constraint_xpaths = {
            "use_limitation": ".//gmd:useLimitation//gco:CharacterString",
            "license": ".//gmd:license//gco:CharacterString",
            "access_constraints": ".//gmd:accessConstraints//gco:CharacterString",
            "use_constraints": ".//gmd:useConstraints//gco:CharacterString",
            "other_constraints": ".//gmd:otherConstraints//gco:CharacterString",
        }

        constraints: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for block in self._all(".//gmd:resourceConstraints//gmd:MD_LegalConstraints"):
            for constraint_type, xpath in constraint_xpaths.items():
                for text_node in self._all(xpath, block):
                    text = self._txt(text_node)
                    if not text:
                        continue

                    normalized = " ".join(text.strip().split())
                    key = (constraint_type, normalized.lower())

                    if key in seen:
                        continue

                    seen.add(key)
                    constraints.append(
                        {
                            "type": constraint_type,
                            "text": normalized,
                        }
                    )

        return constraints

    def _licenses(self) -> list[dict[str, Any]]:
        """
        Lightweight license extraction.
        """

        licenses: list[dict[str, Any]] = []
        seen: set[str] = set()

        for constraint in self._resource_constraints():
            text = constraint.get("text")
            if not isinstance(text, str):
                continue

            found = match_spdx_ids(text)
            for license_id in found:
                if license_id in seen:
                    continue
                seen.add(license_id)
                licenses.append({"id": license_id})

        return licenses


def _normalize_datetime(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if len(text) == 4:
        text = f"{text}-01-01T00:00:00+00:00"
    elif len(text) == 7:
        text = f"{text}-01T00:00:00+00:00"
    elif len(text) == 10 and text.count("-") == 2:
        text = f"{text}T00:00:00+00:00"
    elif text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Failed to parse date %r", value)
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)

    return parsed.isoformat().replace("+00:00", "Z")


def _dedupe_additional_dates(dates: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    output: list[dict[str, str]] = []

    for date in dates:
        date_type = date.get("type")
        value = date.get("value")

        if not isinstance(date_type, str) or not isinstance(value, str):
            continue

        key = (date_type, value)
        if key in seen:
            continue

        seen.add(key)
        output.append({"type": date_type, "value": value})

    return output


def _pick_preferred_datetime(dates: list[dict[str, str]]) -> dict[str, str] | None:
    preferred = (
        "creation",
        "created",
        "publication",
        "published",
        "revision",
        "modified",
        "issued",
        "updated",
        "datestamp",
    )

    by_type: dict[str, str] = {}

    for date in dates:
        by_type.setdefault(date["type"], date["value"])

    for date_type in preferred:
        if date_type in by_type:
            return {"type": date_type, "value": by_type[date_type]}

    return dates[0] if dates else None


def _safe_id(value: str | None) -> str | None:
    if not value:
        return None

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return safe or None


def _load_asset_config(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return _bundled_asset_config()

    path = Path(config_path)
    if not path.exists():
        logger.warning("Asset config does not exist: %s", config_path)
        return _bundled_asset_config()

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed to load asset config: %s", config_path)
        return _bundled_asset_config()


@lru_cache(maxsize=1)
def _bundled_asset_config() -> dict[str, Any]:
    """Load the feature's asset translation table (asset_config.yaml)."""

    path = Path(__file__).parent / "asset_config.yaml"
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("Failed to load bundled asset config: %s", path)
        return {}


def _match_asset_config(href: str, config: dict[str, Any]) -> dict[str, Any] | None:
    lower_href = href.lower()

    for extension, asset_config in (config.get("file_extensions") or {}).items():
        if lower_href.endswith(extension.lower()):
            return asset_config

    for pattern, asset_config in (config.get("url_patterns") or {}).items():
        try:
            if re.search(pattern, href, re.IGNORECASE):
                return asset_config
        except re.error:
            logger.warning("Invalid asset config regex: %s", pattern)
            continue

    return None


def _collection_link(collection_id: str | None) -> list[StacLinkInput]:
    """STAC 1.1 requires a matching ``collection`` link when ``collection`` is set."""
    if not collection_id:
        return []
    return [
        StacLinkInput(
            href=collection_href(collection_id),
            rel="collection",
            type="application/json",
        )
    ]
