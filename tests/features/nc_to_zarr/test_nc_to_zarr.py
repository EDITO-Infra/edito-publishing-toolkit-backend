"""Tests for nc_to_zarr service composition, request validation, and local format integration."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import xarray as xr
from pydantic import ValidationError

from ept.api.dependencies import require_edito_bearer_auth
from ept.api.main import app
from ept.features.nc_to_zarr.models import NetcdfToZarrRequest
from ept.features.nc_to_zarr.service import nc_to_zarr
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from tests.conftest import integration, unit


_CONNECTION = {
    "endpoint_url": "https://s3.example",
    "access_key_id": "ak",
    "secret_access_key": "sk",
    "session_token": "tok",
}


def _s3_request(**overrides: object) -> NetcdfToZarrRequest:
    fields: dict[str, object] = {
        "source_uri": "https://erddap.example/griddap/demo",
        "target": "datasets",
        **_CONNECTION,
    }
    fields.update(overrides)
    return NetcdfToZarrRequest(**fields)


def _fake_dataset() -> object:
    """Minimal stand-in for the open xarray dataset the service logs from."""
    return SimpleNamespace(sizes={}, data_vars=[], coords=[])


@unit
def test_nc_to_zarr_composes_format_primitives_with_default_bucket(monkeypatch):
    calls = []
    dataset = _fake_dataset()

    def fake_open_netcdf(uri, **kwargs):
        calls.append(("open_netcdf", uri, kwargs))
        return dataset

    def fake_create_zarr_store(uri):
        calls.append(("create_zarr_store", uri))
        return uri

    def fake_write_zarr(opened_dataset, target, *, target_uri, options, storage_options):
        calls.append(("write_zarr", target_uri, options, storage_options))
        return type("Result", (), {"target_uri": target_uri})()

    monkeypatch.setattr("ept.features.nc_to_zarr.service.open_netcdf", fake_open_netcdf)
    monkeypatch.setattr("ept.features.nc_to_zarr.service.create_zarr_store", fake_create_zarr_store)
    monkeypatch.setattr("ept.features.nc_to_zarr.service.write_zarr", fake_write_zarr)

    response = nc_to_zarr(
        _s3_request(engine="netcdf4", chunks={"time": 1}, overwrite=True),
        default_bucket="oidc-alice",
    )

    assert response.status == "completed"
    assert response.bucket == "oidc-alice"
    # Store name derived from the source (no .nc to strip, .zarr added) inside target.
    assert response.target_uri == "https://s3.example/oidc-alice/datasets/demo.zarr"
    assert calls[0][0] == "open_netcdf"
    # DAP source (griddap): opened directly, no download, no storage options.
    assert calls[0][1] == "https://erddap.example/griddap/demo"
    assert calls[0][2] == {
        "engine": "netcdf4",
        "chunks": {"time": 1},
        "decode_cf": None,
        "mask_and_scale": None,
    }
    assert calls[1] == ("create_zarr_store", "s3://oidc-alice/datasets/demo.zarr")
    assert calls[2][1] == "s3://oidc-alice/datasets/demo.zarr"
    assert calls[2][2].overwrite is True
    # Resolved request credentials reach the S3 write.
    assert calls[2][3]["key"] == "ak"
    assert calls[2][3]["secret"] == "sk"
    assert calls[2][3]["token"] == "tok"
    assert calls[2][3]["endpoint_url"] == "https://s3.example"


@unit
def test_nc_to_zarr_strips_netcdf_suffix_and_honors_output_name(monkeypatch):
    written = []

    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.download_netcdf",
        lambda uri, **_kwargs: uri,
    )
    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.open_netcdf",
        lambda uri, **_kwargs: _fake_dataset(),
    )
    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.write_zarr",
        lambda _dataset, target, *, target_uri, options, storage_options: written.append(target_uri)
        or type("Result", (), {"target_uri": target_uri})(),
    )

    response = nc_to_zarr(
        _s3_request(source_uri="s3://src-bucket/observations-2025.nc", bucket="oidc-bob", target="zarr"),
        default_bucket="oidc-alice",
    )
    assert response.target_uri == "https://s3.example/oidc-bob/zarr/observations-2025.zarr"

    response = nc_to_zarr(
        _s3_request(source_uri="s3://src-bucket/observations-2025.nc", target="zarr", output_name="demo"),
        default_bucket="oidc-alice",
    )
    assert response.target_uri == "https://s3.example/oidc-alice/zarr/demo.zarr"

    response = nc_to_zarr(
        _s3_request(
            source_uri="s3://src-bucket/observations-2025.nc",
            target="zarr",
            output_name="named.zarr",
        ),
        default_bucket="oidc-alice",
    )
    assert response.target_uri == "https://s3.example/oidc-alice/zarr/named.zarr"

    assert written == [
        "s3://oidc-bob/zarr/observations-2025.zarr",
        "s3://oidc-alice/zarr/demo.zarr",
        "s3://oidc-alice/zarr/named.zarr",
    ]


@unit
def test_nc_to_zarr_resolves_s3_source_with_request_credentials(monkeypatch):
    opened = []
    downloaded = []
    dataset = _fake_dataset()

    def fake_download(uri, *, storage_options):
        downloaded.append((uri, storage_options))
        return "/cache/input.nc"

    def fake_open_netcdf(uri, **kwargs):
        opened.append((uri, kwargs))
        return dataset

    monkeypatch.setattr("ept.features.nc_to_zarr.service.download_netcdf", fake_download)
    monkeypatch.setattr("ept.features.nc_to_zarr.service.open_netcdf", fake_open_netcdf)
    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.write_zarr",
        lambda *_args, **_kwargs: type("Result", (), {"target_uri": "s3://oidc-bob/raw/input.zarr"})(),
    )

    response = nc_to_zarr(
        _s3_request(
            source_uri="s3://src-bucket/input.nc",
            bucket="oidc-bob",
            target="raw",
        )
    )

    assert response.bucket == "oidc-bob"
    # S3 source is downloaded once with request credentials, then opened locally.
    assert downloaded[0][0] == "s3://src-bucket/input.nc"
    assert downloaded[0][1]["key"] == "ak"
    assert downloaded[0][1]["endpoint_url"] == "https://s3.example"
    assert opened[0][0] == "/cache/input.nc"


@unit
def test_nc_to_zarr_local_output_override_needs_no_s3(monkeypatch, tmp_path):
    calls = []
    dataset = _fake_dataset()

    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.open_netcdf",
        lambda uri, **kwargs: calls.append(("open", uri)) or dataset,
    )

    def fail_write_zarr(*_args, **kwargs):
        calls.append(("write", kwargs.get("storage_options")))
        return type("Result", (), {"target_uri": kwargs["target_uri"]})()

    monkeypatch.setattr(
        "ept.features.nc_to_zarr.service.write_zarr",
        fail_write_zarr,
    )

    response = nc_to_zarr(
        NetcdfToZarrRequest(
            source_uri=str(tmp_path / "input.nc"),
            local_output=str(tmp_path / "out"),
        )
    )

    assert response.bucket is None
    assert response.target_uri == str(tmp_path / "out" / "input.zarr")
    # No S3 resolution happened for a pure local conversion.
    assert calls[0] == ("open", str(tmp_path / "input.nc"))
    assert calls[1] == ("write", None)


@unit
def test_nc_to_zarr_requires_bucket_when_no_default_derivable():
    with pytest.raises(ValueError, match="bucket is required"):
        nc_to_zarr(_s3_request(), default_bucket=None)


@unit
def test_nc_to_zarr_rejects_partial_connection_parameters():
    with pytest.raises(ValueError, match="Missing S3 connection fields: secret_access_key"):
        nc_to_zarr(
            NetcdfToZarrRequest(
                source_uri="https://example.com/data.nc",
                target="out",
                endpoint_url="https://s3.example",
                access_key_id="ak",
            ),
            default_bucket="oidc-alice",
        )


@unit
def test_request_requires_target_or_local_output():
    with pytest.raises(ValidationError, match="target is required"):
        NetcdfToZarrRequest(source_uri="/tmp/in.nc", **_CONNECTION)


@unit
def test_request_rejects_s3_and_local_output_together():
    with pytest.raises(ValidationError, match="not both"):
        NetcdfToZarrRequest(
            source_uri="/tmp/in.nc",
            target="out",
            local_output="/tmp/out.zarr",
            **_CONNECTION,
        )


@unit
def test_request_rejects_s3_scheme_in_bucket_and_target():
    with pytest.raises(ValidationError, match="bucket must be a bucket name"):
        NetcdfToZarrRequest(source_uri="/tmp/in.nc", bucket="s3://bucket", target="out")
    with pytest.raises(ValidationError, match="target must be a store path"):
        NetcdfToZarrRequest(source_uri="/tmp/in.nc", bucket="b", target="s3://b/out")


@integration
def test_transform_nc2zarr_writes_local_zarr_store(tmp_path):
    source = tmp_path / "input.nc"
    output_dir = tmp_path / "stores"
    target = output_dir / "input.zarr"
    expected = xr.Dataset(
        {"temperature": (("lat", "lon"), [[10.0, 11.0], [12.0, 13.0]])},
        coords={"lat": [50.0, 51.0], "lon": [2.0, 3.0]},
    )
    expected.to_netcdf(source)

    response = nc_to_zarr(
        NetcdfToZarrRequest(source_uri=str(source), local_output=str(output_dir))
    )

    assert response.status == "completed"
    assert response.target_uri == str(target)
    _assert_zarr_v2_array(target, "temperature", [[10.0, 11.0], [12.0, 13.0]])
    _assert_zarr_v2_array(target, "lat", [50.0, 51.0])
    _assert_zarr_v2_array(target, "lon", [2.0, 3.0])
    assert (target / ".zmetadata").exists()


@integration
def test_transform_nc2zarr_route_converts_local_netcdf_to_local_zarr(tmp_path):
    source = tmp_path / "route-input.nc"
    output_dir = tmp_path / "out"
    target = output_dir / "route-input.zarr"
    expected = xr.Dataset(
        {
            "salinity": (
                ("time", "depth"),
                [[35.1, 35.2], [35.3, 35.4]],
            )
        },
        coords={"time": [0, 1], "depth": [5.0, 10.0]},
        attrs={"title": "Route integration dataset"},
    )
    expected.to_netcdf(source)

    with _authenticated_route():
        response = asyncio.run(
            _post(
                "/v1/transform/nc-to-zarr",
                {
                    "source_uri": str(source),
                    "local_output": str(output_dir),
                },
            )
        )

    assert response.status_code == 200
    assert response.json() == {
        "source_uri": str(source),
        "bucket": None,
        "target_uri": str(target),
        "status": "completed",
    }
    _assert_zarr_v2_array(target, "salinity", [[35.1, 35.2], [35.3, 35.4]])
    _assert_zarr_v2_array(target, "time", [0, 1])
    _assert_zarr_v2_array(target, "depth", [5.0, 10.0])
    root_attrs = json.loads((target / ".zattrs").read_text(encoding="utf-8"))
    assert root_attrs == {"title": "Route integration dataset"}


async def _post(path: str, payload: dict[str, object]) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.post(path, json=payload)


class _authenticated_route:
    def __enter__(self) -> None:
        app.dependency_overrides[require_edito_bearer_auth] = lambda: EditoBearerAuth(
            subject="test-user",
            username="alice",
            claims={"sub": "test-user", "preferred_username": "alice"},
            access_token="access-secret",
        )

    def __exit__(self, *_exc) -> None:
        app.dependency_overrides.pop(require_edito_bearer_auth, None)


def _assert_zarr_v2_array(store, name: str, expected) -> None:
    array_path = store / name
    zarray = json.loads((array_path / ".zarray").read_text(encoding="utf-8"))
    zattrs = json.loads((array_path / ".zattrs").read_text(encoding="utf-8"))
    chunk_key = ".".join("0" for _ in zarray["shape"]) or "0"
    actual = np.frombuffer(
        (array_path / chunk_key).read_bytes(),
        dtype=np.dtype(zarray["dtype"]),
    ).reshape(zarray["shape"])

    assert zarray["zarr_format"] == 2
    assert zarray["compressor"] is None
    assert zattrs["_ARRAY_DIMENSIONS"]
    np.testing.assert_array_equal(actual, np.asarray(expected))
