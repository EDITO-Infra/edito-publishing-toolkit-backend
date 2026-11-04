"""Unit tests for S3 storage primitives and the upload_s3 transfer flow, using fake in-memory filesystems."""

import contextlib
from io import BytesIO
from pathlib import Path

import pytest
from pydantic import ValidationError
from s3fs import S3FileSystem

from ept.features.upload_s3.service import upload_to_s3
from ept.infrastructure.storage.s3 import (
    get_from_s3,
    ls_folder,
    resolve_s3_storage_options,
    rm_from_s3,
    s3_connect,
)
from tests.conftest import unit


class FakeFilesystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def exists(self, path: str) -> bool:
        return path in self.files

    def isdir(self, path: str) -> bool:
        prefix = path.rstrip("/") + "/"
        return any(key.startswith(prefix) for key in self.files)

    def find(self, prefix: str, **_kwargs) -> list[str]:
        prefix = prefix.rstrip("/")
        return [key for key in self.files if key == prefix or key.startswith(prefix + "/")]

    def ls(self, path: str, **_kwargs) -> list[str]:
        path = path.strip("/")
        if not path:
            return sorted({key.split("/")[0] for key in self.files})
        children = {
            key[len(path) + 1 :].split("/")[0] for key in self.files if key.startswith(path + "/")
        }
        return sorted(f"{path}/{child}" for child in children)

    def get_file(self, source: str, target: str, **_kwargs) -> None:
        Path(target).write_bytes(self.files[source])

    def get(self, source: str, target: str, **_kwargs) -> None:
        source = source.rstrip("/")
        matches = [key for key in self.files if key == source or key.startswith(source + "/")]
        if not matches:
            raise FileNotFoundError(source)
        for key in matches:
            relative = key.removeprefix(source).lstrip("/")
            destination = Path(target) / relative if relative else Path(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(self.files[key])

    def put_file(self, source: str, target: str, **_kwargs) -> None:
        self.files[target] = Path(source).read_bytes()

    def rm(self, path: str, recursive: bool = False) -> None:
        if recursive:
            matches = [key for key in self.files if key == path or key.startswith(path + "/")]
            if not matches:
                raise FileNotFoundError(path)
            for key in matches:
                del self.files[key]
            return
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def open(self, path: str, mode: str, **_kwargs):
        if "b" in mode:
            return FakeBinaryFile(self.files, path, mode)
        return FakeTextFile(self.files, path, mode)


class FakeBinaryFile(BytesIO):
    def __init__(self, files: dict[str, bytes], path: str, mode: str) -> None:
        super().__init__(files.get(path, b""))
        self.files = files
        self.path = path
        self._mode = mode

    def __exit__(self, *_args):
        if "w" in self._mode:
            self.files[self.path] = self.getvalue()
        self.close()


class FakeTextFile:
    def __init__(self, files: dict[str, bytes], path: str, mode: str) -> None:
        self.files = files
        self.path = path
        self.mode = mode
        self.value = files.get(path, b"").decode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if "w" in self.mode:
            self.files[self.path] = self.value.encode()

    def read(self) -> str:
        return self.value

    def write(self, value: str) -> None:
        self.value += value


@unit
def test_upload_to_s3_uploads_local_directory(monkeypatch, tmp_path):
    fs = FakeFilesystem()
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "b.txt").write_text("b", encoding="utf-8")
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs", lambda *_args, **_kwargs: (fs, "bucket/root")
    )

    uploaded = upload_to_s3(str(source), "s3://bucket/root")

    assert uploaded == ["s3://bucket/root/a.txt", "s3://bucket/root/nested/b.txt"]
    assert fs.files == {"bucket/root/a.txt": b"a", "bucket/root/nested/b.txt": b"b"}


@unit
def test_upload_to_s3_uploads_local_file_and_protects_existing_target(monkeypatch, tmp_path):
    fs = FakeFilesystem()
    source = tmp_path / "source.txt"
    source.write_text("content", encoding="utf-8")
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs",
        lambda *_args, **_kwargs: (fs, "bucket/source.txt"),
    )

    assert upload_to_s3(str(source), "s3://bucket/source.txt") == ["s3://bucket/source.txt"]
    with pytest.raises(FileExistsError, match="Target already exists"):
        upload_to_s3(str(source), "s3://bucket/source.txt")


@unit
def test_upload_to_s3_streams_http_source(monkeypatch):
    fs = FakeFilesystem()
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs",
        lambda *_args, **_kwargs: (fs, "bucket/path/source.txt"),
    )
    monkeypatch.setattr(
        "ept.infrastructure.storage.s3._open_source_url",
        lambda _source: BytesIO(b"remote content"),
    )

    uploaded = upload_to_s3(
        "https://data.example.test/source.txt",
        "s3://bucket/path/source.txt",
    )

    assert uploaded == ["s3://bucket/path/source.txt"]
    assert fs.files == {"bucket/path/source.txt": b"remote content"}


@unit
def test_upload_to_s3_discovers_http_store_prefix(monkeypatch):
    fs = FakeFilesystem()
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs",
        lambda *_args, **_kwargs: (fs, "bucket/store.zarr"),
    )
    monkeypatch.setattr("ept.features.upload_s3.service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "ept.features.upload_s3.service._list_s3_prefix_keys",
        lambda _base, _bucket, _prefix: ["store.zarr/.zattrs", "store.zarr/data/0"],
    )

    def fake_open(url):
        if url.endswith(".zarr"):
            raise FileNotFoundError(url)
        payload = b"attrs" if url.endswith(".zattrs") else b"chunk0"
        return contextlib.nullcontext(BytesIO(payload))

    monkeypatch.setattr("ept.infrastructure.storage.s3._open_source_url", fake_open)

    uploaded = upload_to_s3("https://host.example.test/bkt/store.zarr", "s3://bucket/store.zarr")

    assert uploaded == ["s3://bucket/store.zarr/.zattrs", "s3://bucket/store.zarr/data/0"]
    assert fs.files["bucket/store.zarr/.zattrs"] == b"attrs"
    assert fs.files["bucket/store.zarr/data/0"] == b"chunk0"


@unit
def test_upload_to_s3_http_missing_source_and_prefix_raises(monkeypatch):
    fs = FakeFilesystem()
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs",
        lambda *_args, **_kwargs: (fs, "bucket/missing.zarr"),
    )
    monkeypatch.setattr("ept.features.upload_s3.service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "ept.features.upload_s3.service._list_s3_prefix_keys",
        lambda _base, _bucket, _prefix: [],
    )
    monkeypatch.setattr(
        "ept.features.upload_s3.service._probe_http_source",
        lambda _url: "plain GET status=404",
    )

    def fake_open(_url):
        raise FileNotFoundError(_url)

    monkeypatch.setattr("ept.infrastructure.storage.s3._open_source_url", fake_open)

    with pytest.raises(FileNotFoundError, match="Source does not exist.*plain GET status=404"):
        upload_to_s3("https://host.example.test/bkt/missing.zarr", "s3://bucket/missing.zarr")


@unit
def test_upload_to_s3_retries_transient_http_failure(monkeypatch):
    fs = FakeFilesystem()
    monkeypatch.setattr(
        "ept.features.upload_s3.service.s3_url_to_fs",
        lambda *_args, **_kwargs: (fs, "bucket/path/source.txt"),
    )
    monkeypatch.setattr("ept.features.upload_s3.service.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "ept.features.upload_s3.service._list_s3_prefix_keys",
        lambda *_args: pytest.fail("transient failure must not fall back to prefix probing"),
    )

    attempts = {"count": 0}

    def flaky_open(_source):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FileNotFoundError(_source)
        return contextlib.nullcontext(BytesIO(b"remote content"))

    monkeypatch.setattr("ept.infrastructure.storage.s3._open_source_url", flaky_open)

    uploaded = upload_to_s3(
        "https://data.example.test/source.txt",
        "s3://bucket/path/source.txt",
    )

    assert attempts["count"] == 2
    assert uploaded == ["s3://bucket/path/source.txt"]
    assert fs.files == {"bucket/path/source.txt": b"remote content"}


@unit
def test_ls_folder_lists_children_under_prefix():
    fs = FakeFilesystem()
    fs.files = {
        "bucket/data/a.nc": b"",
        "bucket/data/sub/b.nc": b"",
        "bucket/other.nc": b"",
    }

    assert ls_folder(fs, "bucket/data") == ["bucket/data/a.nc", "bucket/data/sub"]
    assert ls_folder(fs, "bucket/data", recursive=True) == [
        "bucket/data/a.nc",
        "bucket/data/sub/b.nc",
    ]


@unit
def test_ls_folder_empty_prefix_lists_buckets():
    fs = FakeFilesystem()
    fs.files = {"bkt2/data.nc": b"", "bkt1/a.nc": b""}

    assert ls_folder(fs, "/") == ["bkt1", "bkt2"]
    assert ls_folder(fs, "") == ["bkt1", "bkt2"]


@unit
def test_get_from_s3_downloads_object(tmp_path):
    fs = FakeFilesystem()
    fs.files = {"bucket/data/a.nc": b"payload"}

    target = get_from_s3(fs, "bucket/data/a.nc", tmp_path / "a.nc")

    assert target == tmp_path / "a.nc"
    assert target.read_bytes() == b"payload"


@unit
def test_get_from_s3_downloads_prefix_as_directory(tmp_path):
    fs = FakeFilesystem()
    fs.files = {"bucket/data/a.nc": b"a", "bucket/data/sub/b.nc": b"b"}

    target = get_from_s3(fs, "bucket/data", tmp_path / "data")

    assert target == tmp_path / "data"
    assert (target / "a.nc").read_bytes() == b"a"
    assert (target / "sub" / "b.nc").read_bytes() == b"b"


@unit
def test_rm_s3_objects_deletes_keys_and_prefixes():
    fs = FakeFilesystem()
    fs.files = {
        "bucket/data/a.nc": b"",
        "bucket/data/sub/b.nc": b"",
        "bucket/keep.nc": b"",
    }

    assert rm_from_s3(fs, "bucket/data/a.nc") == ["bucket/data/a.nc"]
    assert rm_from_s3(fs, ["bucket/data"]) == ["bucket/data"]
    assert fs.files == {"bucket/keep.nc": b""}


@unit
def test_rm_s3_objects_missing_key_raises():
    with pytest.raises(FileNotFoundError):
        rm_from_s3(FakeFilesystem(), "bucket/missing.nc")


@unit
def test_s3_connect_accepts_connection_dict_and_validates(monkeypatch):
    for name in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_SESSION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_ENDPOINT_URL",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    fs = s3_connect(
        {
            "endpoint_url": "https://s3.example",
            "access_key_id": "access",
            "secret_access_key": "secret",
        }
    )

    assert isinstance(fs, S3FileSystem)
    with pytest.raises(ValidationError):
        s3_connect({"endpoint_url": "https://s3.example"})


@unit
def test_resolve_s3_storage_options_uses_env(monkeypatch):
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("S3_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setenv("S3_REGION", "eu-west-1")
    monkeypatch.setenv("S3_SESSION_TOKEN", "token")

    assert resolve_s3_storage_options() == {
        "key": "access",
        "secret": "secret",
        "endpoint_url": "https://s3.example",
        "client_kwargs": {"region_name": "eu-west-1"},
        "token": "token",
        "config_kwargs": {
            "connect_timeout": 30,
            "read_timeout": 120,
            "retries": {"max_attempts": 5, "mode": "standard"},
        },
    }


@unit
def test_resolve_s3_storage_options_uses_aws_env_fallback(monkeypatch):
    for name in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_REGION",
        "S3_SESSION_TOKEN",
        "AWS_S3_ENDPOINT",
        "AWS_ENDPOINT_URL_S3",
        "S3_ENDPOINT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://s3.example")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")

    assert resolve_s3_storage_options() == {
        "key": "access",
        "secret": "secret",
        "endpoint_url": "https://s3.example",
        "client_kwargs": {"region_name": "eu-west-1"},
        "token": "token",
        "config_kwargs": {
            "connect_timeout": 30,
            "read_timeout": 120,
            "retries": {"max_attempts": 5, "mode": "standard"},
        },
    }


@unit
def test_resolve_s3_storage_options_requires_non_session_env(monkeypatch):
    for name in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_REGION",
        "S3_SESSION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_S3_ENDPOINT",
        "AWS_ENDPOINT_URL_S3",
        "AWS_ENDPOINT_URL",
        "S3_ENDPOINT",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(
        RuntimeError,
        match="S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY",
    ):
        resolve_s3_storage_options()
