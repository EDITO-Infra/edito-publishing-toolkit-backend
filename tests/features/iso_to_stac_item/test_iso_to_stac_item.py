"""Unit tests for the ISO19139 → STAC Item feature (conversion logic only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import integration, unit
from pydantic import ValidationError

from ept.features.iso_to_stac_item.adapter import Iso19139ToStacOptions, iso19139_to_stac_item_input
from ept.features.iso_to_stac_item.models import IsoToStacItemRequest
from ept.features.iso_to_stac_item.service import iso_to_stac_item
from ept.infrastructure.formats.stac import build_item, validate_item

ISO_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gmd:MD_Metadata xmlns:gmd="http://www.isotc211.org/2005/gmd"
                 xmlns:gco="http://www.isotc211.org/2005/gco"
                 xmlns:gml="http://www.opengis.net/gml">
  <gmd:fileIdentifier>
    <gco:CharacterString>test-dataset-001</gco:CharacterString>
  </gmd:fileIdentifier>
  <gmd:identificationInfo>
    <gmd:MD_DataIdentification>
      <gmd:citation>
        <gmd:CI_Citation>
          <gmd:title><gco:CharacterString>Test Dataset</gco:CharacterString></gmd:title>
          <gmd:date>
            <gmd:CI_Date>
              <gmd:date><gco:DateTime>2024-05-01T12:00:00</gco:DateTime></gmd:date>
              <gmd:dateType><gmd:CI_DateTypeCode codeListValue="creation"/></gmd:dateType>
            </gmd:CI_Date>
          </gmd:date>
        </gmd:CI_Citation>
      </gmd:citation>
      <gmd:abstract><gco:CharacterString>A test dataset.</gco:CharacterString></gmd:abstract>
      <gmd:descriptiveKeywords>
        <gmd:MD_Keywords>
          <gmd:keyword><gco:CharacterString>ocean</gco:CharacterString></gmd:keyword>
          <gmd:keyword><gco:CharacterString>temperature</gco:CharacterString></gmd:keyword>
        </gmd:MD_Keywords>
      </gmd:descriptiveKeywords>
      <gmd:extent>
        <gmd:EX_Extent>
          <gmd:geographicElement>
            <gmd:EX_GeographicBoundingBox>
              <gmd:westBoundLongitude><gco:Decimal>-10.5</gco:Decimal></gmd:westBoundLongitude>
              <gmd:eastBoundLongitude><gco:Decimal>20.25</gco:Decimal></gmd:eastBoundLongitude>
              <gmd:southBoundLatitude><gco:Decimal>35.0</gco:Decimal></gmd:southBoundLatitude>
              <gmd:northBoundLatitude><gco:Decimal>48.75</gco:Decimal></gmd:northBoundLatitude>
            </gmd:EX_GeographicBoundingBox>
          </gmd:geographicElement>
          <gmd:temporalElement>
            <gmd:EX_TemporalExtent>
              <gmd:extent>
                <gml:TimePeriod gml:id="tp1">
                  <gml:beginPosition>2023-01-01</gml:beginPosition>
                  <gml:endPosition>2023-12-31</gml:endPosition>
                </gml:TimePeriod>
              </gmd:extent>
            </gmd:EX_TemporalExtent>
          </gmd:temporalElement>
        </gmd:EX_Extent>
      </gmd:extent>
    </gmd:MD_DataIdentification>
  </gmd:identificationInfo>
  <gmd:distributionInfo>
    <gmd:MD_Distribution>
      <gmd:transferOptions>
        <gmd:MD_DigitalTransferOptions>
          <gmd:onLine>
            <gmd:CI_OnlineResource>
              <gmd:linkage><gmd:URL>https://data.example.org/test.nc</gmd:URL></gmd:linkage>
              <gmd:name><gco:CharacterString>NetCDF download</gco:CharacterString></gmd:name>
            </gmd:CI_OnlineResource>
          </gmd:onLine>
        </gmd:MD_DigitalTransferOptions>
      </gmd:transferOptions>
    </gmd:MD_Distribution>
  </gmd:distributionInfo>
</gmd:MD_Metadata>
"""


def _build_item(xml: str = ISO_XML, **kwargs) -> dict:
    item_input = iso19139_to_stac_item_input(xml, **kwargs)
    item = build_item(item_input)
    validate_item(item)
    return item


@unit
def test_converts_iso_record_to_stac_item() -> None:
    item = _build_item()

    assert item["id"] == "test-dataset-001"
    assert item["type"] == "Feature"
    assert item["bbox"] == [-10.5, 35.0, 20.25, 48.75]
    assert item["geometry"]["type"] == "Polygon"
    assert item["properties"]["title"] == "Test Dataset"
    assert item["properties"]["start_datetime"].startswith("2023-01-01")
    assert item["properties"]["end_datetime"].startswith("2023-12-31")
    assert sorted(item["properties"]["keywords"]) == ["ocean", "temperature"]
    assert "netcdf" in item["assets"]


@unit
def test_collection_only_when_known() -> None:
    without = _build_item()
    assert "collection" not in without

    with_collection = _build_item(
        options=Iso19139ToStacOptions(default_collection="my-collection")
    )
    assert with_collection["collection"] == "my-collection"


@unit
def test_request_collection_id_overrides_options() -> None:
    request = IsoToStacItemRequest(
        source_uri="record.xml",
        target="metadata",
        collection_id="known-collection",
        options=Iso19139ToStacOptions(default_collection="options-collection"),
    )
    assert request.resolved_options().default_collection == "known-collection"


@unit
def test_output_target_or_local_output() -> None:
    # exactly one output: target (S3) or local_output; omitting both is a validation error.
    with pytest.raises(ValidationError, match="target"):
        IsoToStacItemRequest(source_uri="record.xml")

    with pytest.raises(ValidationError, match="not both"):
        IsoToStacItemRequest(source_uri="record.xml", target="metadata", local_output="/tmp")


@unit
def test_missing_bbox_raises() -> None:
    xml = ISO_XML.replace("westBoundLongitude", "missingWestBoundLongitude")
    with pytest.raises(ValueError, match="bounding box"):
        _build_item(xml)


@unit
def test_missing_temporal_raises() -> None:
    xml = (
        ISO_XML.replace("2023-01-01", "not-a-date")
        .replace("2023-12-31", "not-a-date")
        .replace("2024-05-01T12:00:00", "not-a-date")
    )
    with pytest.raises(ValueError, match="temporal"):
        _build_item(xml)


@unit
def test_service_writes_local_output(tmp_path) -> None:
    source = tmp_path / "record.xml"
    source.write_text(ISO_XML, encoding="utf-8")
    output_dir = tmp_path / "out"

    response = iso_to_stac_item(
        IsoToStacItemRequest(source_uri=str(source), target=None, local_output=str(output_dir))
    )

    assert response.status == "completed"
    assert response.bucket is None
    assert response.target_uri == str(output_dir / "test-dataset-001.json")
    written = json.loads(Path(response.target_uri).read_text(encoding="utf-8"))
    assert written["id"] == "test-dataset-001"


@unit
def test_service_namespaces_derived_id_with_username(tmp_path) -> None:
    source = tmp_path / "record.xml"
    source.write_text(ISO_XML, encoding="utf-8")
    output_dir = tmp_path / "out"

    response = iso_to_stac_item(
        IsoToStacItemRequest(source_uri=str(source), target=None, local_output=str(output_dir)),
        default_username="oidc-user",
    )

    # ISO fileIdentifier, slugified and prefixed with the username.
    assert response.item_id == "oidc-user-test-dataset-001"
    assert response.target_uri.endswith("oidc-user-test-dataset-001.json")

    # An explicit item_id is used as-is, no prefix.
    explicit = iso_to_stac_item(
        IsoToStacItemRequest(
            source_uri=str(source), target=None, local_output=str(output_dir), item_id="my-id", overwrite=True
        ),
        default_username="oidc-user",
    )
    assert explicit.item_id == "my-id"


@unit
def test_service_refuses_overwrite(tmp_path) -> None:
    source = tmp_path / "record.xml"
    source.write_text(ISO_XML, encoding="utf-8")
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "test-dataset-001.json").write_text("{}", encoding="utf-8")

    request = IsoToStacItemRequest(source_uri=str(source), target=None, local_output=str(output_dir))
    with pytest.raises(FileExistsError):
        iso_to_stac_item(request)

    request.overwrite = True
    assert iso_to_stac_item(request).status == "completed"
