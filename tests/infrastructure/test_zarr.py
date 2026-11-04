"""Unit tests for Zarr store creation and write behavior."""

import json
import os

import numpy as np
import pytest
import xarray as xr

from ept.infrastructure.formats.zarr import (
    ZarrWriteOptions,
    ZarrWriteResult,
    create_zarr_store,
    write_zarr,
)
from ept.infrastructure.formats.zarr.write import _s3_client
from tests.conftest import unit


class FakeDataset:
    def __init__(self) -> None:
        self.calls = []

    def to_zarr(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})


@unit
def test_create_zarr_store_prepares_local_parent(tmp_path):
    target = tmp_path / "nested" / "output.zarr"

    store = create_zarr_store(str(target))

    assert store == str(target)
    assert target.parent.exists()


@unit
def test_create_zarr_store_returns_remote_uri():
    assert create_zarr_store("s3://bucket/output.zarr") == "s3://bucket/output.zarr"


@unit
def test_s3_client_ignores_ambient_aws_profile(monkeypatch):
    # A host profile env var pointing at a profile that does not exist in
    # ~/.aws/config must not abort request-scoped client construction.
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "ept-missing-test-profile")
    client = _s3_client(
        {
            "key": "test-key",
            "secret": "test-secret",
            "token": "test-token",
            "endpoint_url": "https://s3.example.test",
            "client_kwargs": {"region_name": None},
        }
    )
    assert client.meta.endpoint_url == "https://s3.example.test"


@unit
def test_write_zarr_passes_write_options_to_dataset(monkeypatch):
    dataset = FakeDataset()
    deleted = []
    storage_options = {
        "key": "access",
        "secret": "secret",
        "endpoint_url": "https://s3.example",
        "client_kwargs": {"region_name": "eu-west-1"},
    }
    monkeypatch.setattr(
        "ept.infrastructure.formats.zarr.write._delete_s3_prefix_objects",
        lambda uri, storage_options: deleted.append((uri, storage_options)),
    )

    result = write_zarr(
        dataset,
        "s3://target/output.zarr",
        target_uri="s3://target/output.zarr",
        options=ZarrWriteOptions(consolidated=False, overwrite=True, zarr_format=2),
        storage_options=storage_options,
    )

    assert result == ZarrWriteResult(target_uri="s3://target/output.zarr")
    assert dataset.calls == [
        {
            "args": ("s3://target/output.zarr",),
            "kwargs": {
                "mode": "w-",
                "consolidated": False,
                "storage_options": storage_options,
                "zarr_format": 2,
                "write_empty_chunks": True,
            },
        }
    ]
    assert deleted == [("s3://target/output.zarr", storage_options)]


@unit
def test_write_zarr_uses_exclusive_create_by_default():
    dataset = FakeDataset()

    write_zarr(dataset, "/tmp/demo.zarr", target_uri="/tmp/demo.zarr")

    assert dataset.calls[0]["kwargs"]["mode"] == "w-"


@unit
def test_write_zarr_overwrites_local_symlink_target(tmp_path):
    linked_store = tmp_path / "linked-store"
    linked_store.mkdir()
    target = tmp_path / "output.zarr"
    target.symlink_to(linked_store, target_is_directory=True)
    dataset = xr.Dataset({"temperature": ("x", [1.0, 2.0])})

    write_zarr(
        dataset,
        str(target),
        target_uri=str(target),
        options=ZarrWriteOptions(overwrite=True),
    )

    assert not target.is_symlink()
    assert (target / ".zgroup").exists()
    assert linked_store.exists()


@unit
def test_write_zarr_serializes_bytes_metadata_for_local_v2_store(tmp_path):
    target = tmp_path / "output.zarr"
    values = np.array([b"aa", b"bb"], dtype="S2")
    dataset = xr.Dataset({"station": ("x", values)}, attrs={"source": b"netcdf"})

    write_zarr(
        dataset,
        str(target),
        target_uri=str(target),
    )

    zarray = json.loads((target / "station" / ".zarray").read_text(encoding="utf-8"))
    zattrs = json.loads((target / ".zattrs").read_text(encoding="utf-8"))
    actual = np.frombuffer((target / "station" / "0").read_bytes(), dtype=np.dtype(zarray["dtype"]))

    assert zarray["fill_value"] == ""
    assert zattrs == {"source": "netcdf"}
    np.testing.assert_array_equal(actual, values)


@unit
def test_write_zarr_overwrites_local_target_with_non_writable_child(tmp_path):
    target = tmp_path / "output.zarr"
    locked = target / "locked"
    locked.mkdir(parents=True)
    (locked / "stale").write_text("old", encoding="utf-8")
    os.chmod(locked, 0o500)
    dataset = xr.Dataset({"temperature": ("x", [1.0, 2.0])})

    try:
        write_zarr(
            dataset,
            str(target),
            target_uri=str(target),
            options=ZarrWriteOptions(overwrite=True),
        )
    finally:
        if locked.exists():
            os.chmod(locked, 0o700)

    assert not locked.exists()
    assert (target / ".zgroup").exists()


@unit
def test_write_zarr_reports_existing_local_target_permission_failure(tmp_path, monkeypatch):
    target = tmp_path / "output.zarr"
    locked = target / "locked"
    locked.mkdir(parents=True)
    (locked / "stale").write_text("old", encoding="utf-8")
    os.chmod(locked, 0o500)
    dataset = xr.Dataset({"temperature": ("x", [1.0, 2.0])})

    def deny_chmod(*args):
        raise PermissionError("denied")

    try:
        monkeypatch.setattr(os, "chmod", deny_chmod)

        with pytest.raises(RuntimeError) as excinfo:
            write_zarr(
                dataset,
                str(target),
                target_uri=str(target),
                options=ZarrWriteOptions(overwrite=True),
            )
    finally:
        monkeypatch.undo()
        if locked.exists():
            os.chmod(locked, 0o700)

    assert "Failed to write Zarr store" in str(excinfo.value)
    assert "Cannot remove existing Zarr target" in str(excinfo.value.__cause__)
