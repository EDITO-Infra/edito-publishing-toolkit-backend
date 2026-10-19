"""Unit tests for the ARCO STAC Item feature."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel, ValidationError

from ept.features.arco_stac_item.conversions import (
    CONVERSIONS,
    Conversion,
    detect_conversion,
    resolve_conversion,
)
from ept.features.arco_stac_item.models import ArcoConversionResult, ArcoStacItemRequest
from ept.features.arco_stac_item.service import (
    _convert,
    _explicit_conversion,
    _Outputs,
    _plan_conversions,
    _resolve_assets_target,
    _stale_generated_keys,
    _unique_asset_key,
)
from tests.conftest import unit


def _item(assets: dict) -> dict:
    return {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": "my-dataset",
        "geometry": None,
        "bbox": [0.0, 0.0, 1.0, 1.0],
        "properties": {"datetime": "2024-01-01T00:00:00Z"},
        "links": [],
        "assets": assets,
    }


def _request(**extra) -> ArcoStacItemRequest:
    return ArcoStacItemRequest(source_uri="s3://b/items/x.json", item_target="out", **extra)


@unit
def test_conversion_registry_declares_expected_features():
    assert set(CONVERSIONS) == {"nc-to-zarr", "gdb-to-parquet", "shp-to-parquet", "upload"}
    nc = resolve_conversion("nc-to-zarr")
    assert nc.media_type == "application/vnd+zarr"
    assert nc.detect_suffixes == (".nc", ".nc4", ".cdf")
    assert resolve_conversion("nope") is None


@unit
def test_conversion_builds_declared_sub_requests():
    creds = {"endpoint_url": "https://minio", "access_key_id": "ak", "secret_access_key": "sk", "session_token": "tok"}
    nc = resolve_conversion("nc-to-zarr")
    sub = nc.build_request(
        href="https://erddap.example.org/a.nc",
        bucket="bkt",
        target="prefix",
        source_key="my-data",
        s3_prefix="s3://bkt/prefix",
        suffix=".nc",
        credentials=creds,
        overwrite=True,
    )
    assert sub.source_uri == "https://erddap.example.org/a.nc"
    assert sub.bucket == "bkt"
    assert sub.target == "prefix"
    assert sub.output_name == "my-data.zarr"
    assert sub.session_token == "tok"
    assert sub.overwrite is True

    gdb = resolve_conversion("gdb-to-parquet")
    sub = gdb.build_request(
        href="https://x.org/a.gdb.zip",
        bucket="bkt",
        target="prefix",
        source_key="zip",
        s3_prefix="s3://bkt/prefix",
        suffix=".zip",
        credentials=creds,
        overwrite=True,
    )
    assert sub.source_uri == "https://x.org/a.gdb.zip"
    assert sub.bucket == "bkt"
    assert sub.target == "prefix/zip"
    assert sub.overwrite is True

    up = resolve_conversion("upload")
    sub = up.build_request(
        href="https://x.org/a.zip?token=1",
        bucket="bkt",
        target="prefix",
        source_key="data",
        s3_prefix="s3://bkt/prefix",
        suffix=".zip",
        credentials=creds,
        overwrite=False,
    )
    assert sub.source == "https://x.org/a.zip?token=1"
    assert sub.output_name == "data.zip"
    assert sub.session_token == "tok"
    assert sub.overwrite is False


@unit
def test_conversion_credentials_merge_before_validation():
    creds = {"endpoint_url": "https://minio", "access_key_id": "ak", "secret_access_key": "sk", "session_token": "tok"}
    up = resolve_conversion("upload")
    # upload_s3 declares the credentials required — they must arrive pre-validation.
    sub = up.build_request(
        href="https://x.org/a.zip",
        bucket="bkt",
        target="p",
        source_key="a",
        s3_prefix="s3://bkt/p",
        suffix=".zip",
        credentials=creds,
        overwrite=False,
    )
    assert sub.session_token == "tok"
    assert sub.overwrite is False


@unit
def test_auto_detection_uses_declared_rules():
    assert detect_conversion(href="https://erddap.example.org/data.nc", media_type="") is CONVERSIONS["nc-to-zarr"]
    assert (
        detect_conversion(href="https://x.org/dods/a", media_type="application/x-netcdf")
        is CONVERSIONS["nc-to-zarr"]
    )
    assert detect_conversion(href="https://x.org/data.gdb.zip", media_type="") is CONVERSIONS["gdb-to-parquet"]
    # Ambiguous or unrelated assets detect nothing.
    assert detect_conversion(href="https://x.org/data.zip", media_type="application/zip") is None
    assert detect_conversion(href="https://x.org/map.tif", media_type="image/tiff") is None


@unit
def test_explicit_mappings_win_and_validate_against_the_registry():
    item = _item(
        {
            "download": {"href": "https://x.org/data.zip"},
            "remote": {"href": "https://y.org/other.zip"},
        }
    )
    request = _request(conversions={"download": "gdb-to-parquet", "other": "upload"})
    plans = _plan_conversions(item, request)
    assert [(k, c.key) for k, _, c in plans] == [
        ("download", "gdb-to-parquet"),
        ("remote", "upload"),
    ]
    # Unknown conversion keys are rejected at request construction...
    with pytest.raises(ValidationError, match="Unknown conversion"):
        _request(conversions={"download": "to-parquet"})
    # ...and the service-level lookup stays defensive.
    with pytest.raises(ValueError, match="Unknown conversion"):
        _explicit_conversion("download", item["assets"]["download"], {"download": "to-parquet"})


@unit
def test_plain_zip_is_never_auto_converted():
    item = _item({"download": {"href": "https://x.org/data.zip"}})
    assert _plan_conversions(item, _request()) == []
    request = _request(conversions={"data.zip": "gdb-to-parquet"})
    assert [(k, c.key) for k, _, c in _plan_conversions(item, request)] == [("download", "gdb-to-parquet")]


@unit
def test_explicit_name_does_not_match_directory_segments():
    """Re-running ARCO on an Item that already holds generated outputs: the
    mapping key "zip" matches the source asset, but not the "/zip/" directory
    inside the generated parquet's href — which would otherwise re-convert
    that parquet as a shapefile and fail the request (422)."""

    item = _item(
        {
            "zip": {"href": "https://files.example.org/data/mangrove.zip"},
            "parquet": {"href": "https://minio.dive.edito.eu/bkt/out/zip/mangrove.parquet"},
        }
    )
    request = _request(conversions={"zip": "shp-to-parquet"})
    assert [(k, c.key) for k, _, c in _plan_conversions(item, request)] == [("zip", "shp-to-parquet")]
    # A fragment containing "/" still matches anywhere in the href path.
    deep = _request(conversions={"out/zip/mangrove.parquet": "shp-to-parquet"})
    assert [(k, c.key) for k, _, c in _plan_conversions(item, deep)] == [("parquet", "shp-to-parquet")]


@unit
def test_asset_key_collision_gets_numeric_suffix_but_upload_repoints():
    assert _unique_asset_key("zarr", "a", {"zarr"}) == "zarr-2"
    assert _unique_asset_key("parquet", "a", {"parquet", "parquet-2"}) == "parquet-3"
    assert _unique_asset_key("zarr", "a", {"data"}) == "zarr"
    # Upload re-points its own source key even though it is already taken.
    assert _unique_asset_key("download", "download", {"download"}) == "download"


@unit
def test_convert_yields_one_result_per_output_file_not_the_prefix():
    """Multi-file converters (gdb/shp → parquet) report per-file URLs in
    ``outputs``; their ``target_uri`` is only the directory prefix, which
    must never become an asset href."""

    class _StubRequest(BaseModel):
        source_uri: str
        bucket: str
        target: str
        overwrite: bool = False

    class _StubResponse:
        target_uri: ClassVar[str] = "https://minio/bkt/out/zip"
        outputs: ClassVar[list[str]] = [
            "https://minio/bkt/out/zip/layer_a.parquet",
            "https://minio/bkt/out/zip/layer_b.parquet",
        ]
        layers: ClassVar[list[str]] = ["layer_a", "layer_b"]

    parquet = Conversion(
        key="shp-to-parquet",
        service=lambda _request: _StubResponse(),
        request_model=_StubRequest,
        asset_key="parquet",
        media_type="application/vnd.apache.parquet",
        fields={"href": "source_uri", "bucket": "bucket", "target": "target"},
        values={"target": "{target}/{source}"},
        result_field="layers",
    )
    request = _request()
    results = _convert(
        request,
        _Outputs(bucket="bkt", assets_target="out"),
        "zip",
        {"href": "https://x.org/a.zip"},
        parquet,
    )
    assert [result.target_uri for result in results] == _StubResponse.outputs
    assert [result.asset_key for result in results] == ["parquet", "parquet"]
    assert [result.layers for result in results] == [["layer_a"], ["layer_b"]]

    # Converters without an outputs field keep pointing at their target_uri.
    class _PrefixOnlyResponse:
        target_uri = "https://minio/bkt/out/data.zarr"

    zarr = Conversion(
        key="nc-to-zarr",
        service=lambda _request: _PrefixOnlyResponse(),
        request_model=_StubRequest,
        asset_key="zarr",
        media_type="application/vnd+zarr",
        fields={"href": "source_uri", "bucket": "bucket", "target": "target"},
        values={},
    )
    results = _convert(
        request,
        _Outputs(bucket="bkt", assets_target="out"),
        "data",
        {"href": "https://x.org/a.nc"},
        zarr,
    )
    assert [result.target_uri for result in results] == ["https://minio/bkt/out/data.zarr"]
    assert [result.layers for result in results] == [None]


@unit
def test_stale_generated_keys_match_the_conversion_key_family():
    item = _item(
        {
            "zip": {"href": "https://x.org/data.zip"},
            "parquet": {"href": "https://minio/bkt/out/zip"},
            "parquet-2": {"href": "https://minio/bkt/out/zip/a.parquet"},
            "parquet-old": {"href": "https://minio/bkt/elsewhere.parquet"},
            "other": {"href": "https://x.org/other.nc"},
        }
    )
    results = [
        ArcoConversionResult(
            source_asset_key="zip",
            feature="shp-to-parquet",
            asset_key="parquet",
            target_uri="https://minio/bkt/out/zip/a.parquet",
            layers=["a"],
        )
    ]
    stale = _stale_generated_keys(item, results, set(item["assets"]))
    # The numbered variants of the declared key are previous-run output;
    # the source asset, a non-numeric suffix, and unrelated assets are not.
    assert stale == ["parquet", "parquet-2"]


@unit
def test_assets_target_resolution():
    assert _resolve_assets_target(_request()) == "out"
    assert _resolve_assets_target(_request(assets_target="zip")) == "zip"
    derived = ArcoStacItemRequest(source_uri="s3://b/prefix/item.json")
    assert _resolve_assets_target(derived) == "prefix"
    with pytest.raises(ValueError, match="assets_target is required"):
        _resolve_assets_target(ArcoStacItemRequest(source_uri="/local/item.json"))


@unit
def test_request_validates_bucket_and_targets():
    with pytest.raises(ValidationError):
        ArcoStacItemRequest(source_uri="x", item_target="t", bucket="s3://b")
    with pytest.raises(ValidationError):
        ArcoStacItemRequest(source_uri="x", item_target="s3://b/t")
    with pytest.raises(ValidationError, match="Unknown conversion"):
        ArcoStacItemRequest(source_uri="x", item_target="t", conversions={"a": "to-parquet"})


@unit
def test_item_output_location_is_choosable_or_in_place():
    request = _request(bucket="b")
    assert request.resolved_item_uri("s3://b/items/x.json", "x") == "s3://b/out/x.json"
    in_place = ArcoStacItemRequest(source_uri="s3://b/items/x.json")
    assert in_place.resolved_item_uri("s3://b/items/x.json", "x") == "s3://b/items/x.json"
    with pytest.raises(ValueError, match="item_target is required"):
        ArcoStacItemRequest(source_uri="/local/item.json").resolved_item_uri("/local/item.json", "i")
    with pytest.raises(ValueError, match="bucket is required"):
        _request().resolved_item_uri("s3://b/items/x.json", "x")
