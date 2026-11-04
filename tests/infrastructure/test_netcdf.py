"""Unit tests for NetCDF format primitives.

These tests replace xarray's open_dataset and fsspec so no network resources
are needed while checking the primitive call contracts.
"""

import numpy as np
import pytest
import xarray as xr

import ept.infrastructure.formats.netcdf.read as read
from ept.infrastructure.formats.netcdf import download_netcdf, is_dap_source, open_netcdf
from tests.conftest import unit


def _patch_open_dataset(monkeypatch, opened: list) -> None:
    monkeypatch.setattr(
        read.xr,
        "open_dataset",
        lambda source, **kwargs: opened.append((source, kwargs)) or xr.Dataset(),
    )


@unit
def test_open_netcdf_routes_dap_endpoints_to_netcdf4_engine(monkeypatch):
    opened = []
    _patch_open_dataset(monkeypatch, opened)

    open_netcdf("https://erddap.example/griddap/demo", engine="netcdf4", decode_cf=True)

    assert opened == [
        ("https://erddap.example/griddap/demo", {"engine": "netcdf4", "decode_cf": True})
    ]


@unit
def test_open_netcdf_routes_plain_http_to_h5netcdf(monkeypatch):
    opened = []
    _patch_open_dataset(monkeypatch, opened)

    open_netcdf("https://example.org/files/data.nc")

    assert opened == [("https://example.org/files/data.nc", {"engine": "h5netcdf"})]


@unit
def test_open_netcdf_routes_s3_to_h5netcdf_with_storage_options(monkeypatch):
    opened = []
    _patch_open_dataset(monkeypatch, opened)

    open_netcdf("s3://bucket/input.nc", storage_options={"endpoint_url": "https://s3.example"})

    assert opened == [
        (
            "s3://bucket/input.nc",
            {
                "engine": "h5netcdf",
                "storage_options": {"endpoint_url": "https://s3.example"},
            },
        )
    ]


@unit
def test_open_netcdf_passes_local_path_directly_to_xarray(monkeypatch, tmp_path):
    opened = []
    _patch_open_dataset(monkeypatch, opened)

    source = tmp_path / "input.nc"
    actual = open_netcdf(str(source), chunks={"time": 1}, mask_and_scale=False)

    assert opened == [(str(source), {"chunks": {"time": 1}, "mask_and_scale": False})]


@unit
def test_open_netcdf_rejects_non_netcdf_paths():
    with pytest.raises(ValueError, match="uri must point to a NetCDF file"):
        open_netcdf("/local/path/store.zarr")


@unit
def test_open_netcdf_accepts_existing_local_file_without_suffix(monkeypatch, tmp_path):
    """download_netcdf cache entries are hash-named without a NetCDF suffix."""
    opened = []
    _patch_open_dataset(monkeypatch, opened)

    cached = tmp_path / "58bbda0b34532664fd93be910f18a92a2"
    cached.write_bytes(b"not-checked-here")

    open_netcdf(str(cached))

    assert opened == [(str(cached), {})]


@unit
def test_download_netcdf_uses_simplecache_with_storage_options(monkeypatch):
    calls = []

    def fake_open_local(path, **kwargs):
        calls.append((path, kwargs))
        return "/cache/abc123"

    monkeypatch.setattr(read.fsspec, "open_local", fake_open_local)

    local = download_netcdf(
        "s3://source/input.nc", storage_options={"endpoint_url": "https://s3.example"}
    )

    assert local == "/cache/abc123"
    path, kwargs = calls[0]
    assert path == "simplecache::s3://source/input.nc"
    assert kwargs["simplecache"]["cache_storage"] == "/tmp/ept-nc-cache"
    assert kwargs["s3"] == {"endpoint_url": "https://s3.example"}


@unit
def test_summarize_netcdf_falls_back_to_download_for_classic_files(tmp_path, monkeypatch):
    """netCDF-3-style HTTP sources: h5netcdf fails, the download path opens it."""
    real_open_dataset = xr.open_dataset
    downloaded = tmp_path / "downloaded.nc"

    def fake_open_dataset(path, **kwargs):
        if kwargs.get("engine") == "h5netcdf":
            raise OSError("not HDF5")
        return real_open_dataset(path, **kwargs)

    xr.Dataset({"sst": (("x",), np.zeros(2))}).to_netcdf(downloaded)

    monkeypatch.setattr(read.xr, "open_dataset", fake_open_dataset)
    monkeypatch.setattr(read, "download_netcdf", lambda _uri, **_kw: str(downloaded))

    summary = read.summarize_netcdf("https://erddap.example.org/files/ds/data.nc")

    assert list(summary.variables) == ["sst"]


@unit
def test_is_dap_source_distinguishes_endpoints_from_files():
    assert is_dap_source("https://erddap.example/erddap/griddap/demo")
    assert is_dap_source("https://thredds.example/dodsC/set/data")
    assert is_dap_source(
        "http://opendap.example/thredds/dodsC/data/set/Water_body_phosphate.4Danl.nc"
    )
    assert is_dap_source("https://example.org/sosC/observations")
    assert not is_dap_source("https://erddap.example/erddap/griddap/demo.nc?var=t")
    assert not is_dap_source("https://example.com/data/water.nc")
    assert not is_dap_source("/local/path/data.nc")


@unit
def test_thredds_file_server_url_rewrites_dodsc_only():
    assert (
        read.thredds_file_server_url(
            "http://opendap.example/thredds/dodsC/data/set/Water_body_phosphate.4Danl.nc"
        )
        == "http://opendap.example/thredds/fileServer/data/set/Water_body_phosphate.4Danl.nc"
    )
    assert read.thredds_file_server_url("http://example.com/data/water.nc") is None
    assert read.thredds_file_server_url("/local/path/data.nc") is None
