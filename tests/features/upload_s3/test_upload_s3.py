"""Tests for upload_s3 service behavior and live S3 uploads."""

import asyncio
import contextlib
import io
import os
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError

from ept.features.upload_s3.models import UploadS3Request
from ept.features.upload_s3.service import upload_s3
from ept.features.upload_s3.service import optimized_upload_params, upload_transfer_profile
from ept.infrastructure.services.edito_auth import (
    S3ConnectionParameters,
    exchange_s3_credentials,
)
from ept.infrastructure.storage.s3 import put_to_s3, resolve_s3_storage_options
from tests.conftest import integration, unit


@unit
def test_upload_s3_uses_bucket_relative_target_and_connection_parameters(monkeypatch):
    calls: list[tuple[tuple, dict]] = []

    def fake_upload(*args, **kwargs):
        calls.append((args, kwargs))
        return ["s3://bucket/data/input.nc"]

    monkeypatch.setattr("ept.features.upload_s3.service.upload_to_s3", fake_upload)

    response = upload_s3(
        _upload_request(
            bucket="bucket",
            source="/data/input.nc",
            target="data",
            connection=_test_connection(),
        )
    )

    assert response.status == "completed"
    assert response.source == "/data/input.nc"
    assert response.target_uri == "s3://bucket/data/input.nc"
    assert response.uploaded == ["s3://bucket/data/input.nc"]
    assert calls == [
        (
            ("/data/input.nc", "s3://bucket/data/input.nc"),
            {
                "recursive": True,
                "overwrite": False,
                "storage_options": resolve_s3_storage_options(_test_connection()),
            },
        )
    ]


@unit
def test_upload_s3_derives_name_from_url(monkeypatch):
    calls: list[tuple] = []

    def fake_upload(*args, **_kwargs):
        calls.append(args)
        return ["s3://bucket/imports/input.nc"]

    monkeypatch.setattr("ept.features.upload_s3.service.upload_to_s3", fake_upload)

    response = upload_s3(
        _upload_request(
            bucket="bucket",
            source="https://data.example.test/files/input.nc?download=true",
            target="imports",
            connection=_test_connection(),
        )
    )

    assert response.target_uri == "s3://bucket/imports/input.nc"
    assert calls == [
        (
            "https://data.example.test/files/input.nc?download=true",
            "s3://bucket/imports/input.nc",
        )
    ]


@unit
def test_upload_schema_exposes_separate_connection_inputs():
    schema = UploadS3Request.model_json_schema()

    assert "connection" not in schema["properties"]
    assert {
        "source",
        "target",
        "endpoint_url",
        "access_key_id",
        "secret_access_key",
    } <= set(schema["required"])
    # Bucket defaults to the caller's personal oidc-<username> bucket.
    assert "bucket" not in schema["required"]
    # Static-key S3 connections work without a session token.
    assert "session_token" not in schema["required"]
    assert "remote_source" not in schema["properties"]
    assert "local_source" not in schema["properties"]
    assert "source_path" not in schema["properties"]
    assert "target_uri" not in schema["properties"]


@unit
def test_upload_request_without_session_token_omits_s3fs_token():
    request = UploadS3Request(
        bucket="bucket",
        source="/tmp/data",
        target="prefix",
        endpoint_url="https://s3.example.test",
        access_key_id="static-key",
        secret_access_key="static-secret",
    )

    assert request.session_token is None
    assert "token" not in request.storage_options()


@unit
def test_upload_request_rejects_bucket_repeated_in_target():
    with pytest.raises(ValidationError, match="target must be relative to bucket"):
        _upload_request(
            bucket="bucket",
            source="https://data.example.test/input.nc",
            target="s3://bucket/data/input.nc",
            connection=_test_connection(),
        )

@unit
def test_upload_request_source_auto_detection():
    base = {
        "bucket": "bucket",
        "target": "data",
        **_test_connection().model_dump(),
    }
    # A URL is accepted as remote.
    remote = UploadS3Request.model_validate(
        {**base, "source": "https://data.example.test/input.nc"}
    )
    assert remote.source == "https://data.example.test/input.nc"
    # A filesystem path is accepted as local.
    local = UploadS3Request.model_validate({**base, "source": "/data/input.nc"})
    assert local.source == "/data/input.nc"
    # Other URL schemes are rejected.
    with pytest.raises(ValidationError, match="source must be an http"):
        UploadS3Request.model_validate({**base, "source": "s3://bucket/data/input.nc"})
    with pytest.raises(ValidationError, match="source must be an http"):
        UploadS3Request.model_validate({**base, "source": "ftp://data.example.test/input.nc"})
    with pytest.raises(ValidationError, match="source is required"):
        UploadS3Request.model_validate({**base, "source": "   "})


@unit
def test_upload_s3_defaults_to_personal_oidc_bucket(monkeypatch):
    monkeypatch.setattr(
        "ept.features.upload_s3.service.upload_to_s3",
        lambda *args, **kwargs: [args[1]],
    )

    response = upload_s3(
        _upload_request(
            bucket=None,
            source="https://data.example.test/input.nc",
            target="my-uploads",
            connection=_test_connection(),
        ),
        default_bucket="oidc-alice",
    )

    assert response.bucket == "oidc-alice"
    assert response.target_uri == "s3://oidc-alice/my-uploads/input.nc"


@unit
def test_upload_s3_explicit_bucket_beats_default(monkeypatch):
    monkeypatch.setattr(
        "ept.features.upload_s3.service.upload_to_s3",
        lambda *args, **kwargs: [args[1]],
    )

    response = upload_s3(
        _upload_request(
            bucket="oidc-myproject",
            source="https://data.example.test/input.nc",
            target="my-uploads",
            connection=_test_connection(),
        ),
        default_bucket="oidc-alice",
    )

    assert response.bucket == "oidc-myproject"
    assert response.target_uri == "s3://oidc-myproject/my-uploads/input.nc"


@unit
def test_upload_s3_requires_bucket_when_no_default_derivable(monkeypatch):
    monkeypatch.setattr(
        "ept.features.upload_s3.service.upload_to_s3",
        lambda *args, **kwargs: [args[1]],
    )

    with pytest.raises(ValueError, match="bucket is required"):
        upload_s3(
            _upload_request(
                bucket=None,
                source="https://data.example.test/input.nc",
                target="my-uploads",
                connection=_test_connection(),
            ),
            default_bucket=None,
        )


@unit
def test_request_connection_parameters_work_without_server_credentials(monkeypatch):
    for name in (
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "S3_SESSION_TOKEN",
        "S3_REGION",
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
    monkeypatch.setenv("AWS_PROFILE", "custom-s3")

    assert resolve_s3_storage_options(_test_connection()) == {
        "key": "temporary-key",
        "secret": "temporary-secret",
        "token": "temporary-token",
        "endpoint_url": "https://s3.example.test",
        "client_kwargs": {"region_name": None},
        "config_kwargs": {
            "connect_timeout": 30,
            "read_timeout": 120,
            "retries": {"max_attempts": 5, "mode": "standard"},
        },
        "session_vars": {"profile": (None, None, None, None)},
    }


@unit
def test_upload_connection_rejects_sts_metadata():
    with pytest.raises(ValidationError):
        UploadS3Request.model_validate(
            {
                "bucket": "bucket",
                "source": "/data/input.nc",
                "target": "data",
                **_test_connection().model_dump(),
                "expiration": "2026-09-05T12:00:00Z",
            }
        )


@integration
def test_upload_s3_live_uploads_isolated_temp_file(tmp_path):
    bucket, target = _require_live_s3_target()
    target_uri = f"s3://{bucket}/{target}/upload-source.txt"
    source = tmp_path / "upload-source.txt"
    source.write_text("upload integration test\n", encoding="utf-8")

    response = upload_s3(
        _upload_request(
            bucket=bucket,
            source=str(source),
            target=target,
            overwrite=True,
            connection=_live_s3_connection(),
        )
    )

    assert response.status == "completed"
    assert response.source == str(source)
    assert response.target_uri == target_uri
    assert response.uploaded == [target_uri]


@unit
def test_upload_s3_output_name_overrides_url_derived_name(monkeypatch):
    calls: list[tuple] = []

    def fake_upload(*args, **_kwargs):
        calls.append(args)
        return ["s3://bucket/imports/myxml.xml"]

    monkeypatch.setattr("ept.features.upload_s3.service.upload_to_s3", fake_upload)

    response = upload_s3(
        _upload_request(
            bucket="bucket",
            source=(
                "https://emodnet.ec.europa.eu/geonetwork/srv/api/records/"
                "8201070b-4b0b-4d54-8910-abcea5dce57f/formatters/xml"
            ),
            target="imports",
            output_name="myxml.xml",
            connection=_test_connection(),
        )
    )

    assert response.target_uri == "s3://bucket/imports/myxml.xml"
    assert calls == [
        (
            "https://emodnet.ec.europa.eu/geonetwork/srv/api/records/"
            "8201070b-4b0b-4d54-8910-abcea5dce57f/formatters/xml",
            "s3://bucket/imports/myxml.xml",
        )
    ]


@unit
def test_upload_request_rejects_path_like_output_name():
    with pytest.raises(ValidationError, match="output_name must be a single"):
        _upload_request(
            bucket="bucket",
            source="https://data.example.test/input.nc",
            target="data",
            output_name="../escape.xml",
            connection=_test_connection(),
        )


@unit
def test_upload_transfer_profile_and_params(tmp_path):
    from ept.infrastructure.storage.s3 import S3_PUT_CHUNK_BYTES
    from ept.features.upload_s3.service import (
        S3_UPLOAD_LARGE_MAX_CONCURRENCY,
        S3_UPLOAD_SMALL_CHUNK_BYTES,
        S3_UPLOAD_SMALL_MAX_CONCURRENCY,
    )

    small = tmp_path / "a.bin"
    small.write_bytes(b"x" * 512 * 1024)
    large = tmp_path / "b.bin"
    large.write_bytes(b"x" * 300 * 1024 * 1024)

    profile = upload_transfer_profile([(small, "bucket/a"), (large, "bucket/b"), (tmp_path / "gone", "bucket/c")])
    assert profile == {
        "file_count": 2,
        "total_bytes": small.stat().st_size + large.stat().st_size,
        "avg_size": (small.stat().st_size + large.stat().st_size) // 2,
    }

    # Small average -> minimum parts, high concurrency
    assert optimized_upload_params({"file_count": 10, "total_bytes": 0, "avg_size": 0}) == (
        S3_UPLOAD_SMALL_CHUNK_BYTES,
        S3_UPLOAD_SMALL_MAX_CONCURRENCY,
    )
    # Many files -> same even with a larger average
    assert optimized_upload_params({"file_count": 1001, "total_bytes": 0, "avg_size": 5 * 2**20}) == (
        S3_UPLOAD_SMALL_CHUNK_BYTES,
        S3_UPLOAD_SMALL_MAX_CONCURRENCY,
    )
    # Large files -> size cap stays at 20 MiB, concurrency scales up
    assert optimized_upload_params({"file_count": 2, "total_bytes": 0, "avg_size": 150 * 2**20}) == (
        S3_PUT_CHUNK_BYTES,
        S3_UPLOAD_LARGE_MAX_CONCURRENCY,
    )


@unit
def test_put_to_s3_skips_exists_check_when_overwrite(monkeypatch):
    class _FailingFS:
        def exists(self, _remote_path):
            raise AssertionError("fs.exists must not be called when overwrite=true")

        def open(self, _remote_path, _mode):
            return contextlib.nullcontext(io.BytesIO())

    monkeypatch.setattr(
        "ept.infrastructure.storage.s3._open_source_url",
        lambda _url: contextlib.nullcontext(io.BytesIO(b"payload")),
    )

    uri = put_to_s3(_FailingFS(), "https://data.example.test/file", "bucket/key", overwrite=True)

    assert uri == "s3://bucket/key"


@unit
def test_put_to_s3_headobject_403_raises_actionable_error_without_overwrite():
    from botocore.exceptions import ClientError

    class _ForbiddenFS:
        def exists(self, _remote_path):
            raise ClientError(
                {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
            )

    with pytest.raises(FileExistsError, match="overwrite=true"):
        put_to_s3(_ForbiddenFS(), "https://data.example.test/file", "bucket/key", overwrite=False)


@unit
def test_put_to_s3_s3fs_permission_error_raises_actionable_error_without_overwrite():
    # s3fs _error_wrapper converts the botocore 403 ClientError into a plain
    # PermissionError before callers see it.
    class _ForbiddenFS:
        def exists(self, _remote_path):
            raise PermissionError("Forbidden")

    with pytest.raises(FileExistsError, match="overwrite=true"):
        put_to_s3(_ForbiddenFS(), "https://data.example.test/file", "bucket/key", overwrite=False)


@unit
def test_put_to_s3_headobject_404_treated_as_missing():
    from botocore.exceptions import ClientError

    class _NotFoundFS:
        def exists(self, _remote_path):
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
            )

        def open(self, _remote_path, _mode):
            return contextlib.nullcontext(io.BytesIO())

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            "ept.infrastructure.storage.s3._open_source_url",
            lambda _url: contextlib.nullcontext(io.BytesIO(b"payload")),
        )
        uri = put_to_s3(_NotFoundFS(), "https://data.example.test/file", "bucket/key", overwrite=False)

    assert uri == "s3://bucket/key"


def _upload_request(
    *,
    bucket: str | None,
    source: str,
    target: str,
    connection: S3ConnectionParameters,
    overwrite: bool = False,
    output_name: str | None = None,
) -> UploadS3Request:
    return UploadS3Request(
        bucket=bucket,
        source=source,
        target=target,
        overwrite=overwrite,
        output_name=output_name,
        endpoint_url=connection.endpoint_url,
        access_key_id=connection.access_key_id,
        secret_access_key=connection.secret_access_key,
        session_token=connection.session_token,
    )


def _test_connection() -> S3ConnectionParameters:
    return S3ConnectionParameters(
        endpoint_url="https://s3.example.test",
        access_key_id="temporary-key",
        secret_access_key="temporary-secret",
        session_token="temporary-token",
    )


def _live_s3_connection() -> S3ConnectionParameters:
    username = os.getenv("EDITO_USERNAME", "").strip().strip("\"'")
    password = os.getenv("EDITO_PASSWORD", "").strip().strip("\"'")
    if not username or not password:
        pytest.skip("Set EDITO_USERNAME and EDITO_PASSWORD to run this live S3 check.")

    credentials = asyncio.run(exchange_s3_credentials(username, password))
    return S3ConnectionParameters.model_validate(
        credentials.model_dump(exclude={"expiration"})
    )


def _require_live_s3_target() -> tuple[str, str]:
    value = os.getenv("TEST_S3_TARGET_URI", "").strip().strip("\"'")
    if not value:
        pytest.skip("Set TEST_S3_TARGET_URI to run this live S3 integration check.")
    parsed = urlsplit(value)
    target = parsed.path.strip("/")
    if parsed.scheme != "s3" or not parsed.netloc or not target:
        pytest.fail("TEST_S3_TARGET_URI must be an s3://bucket/object-key URI.")
    return parsed.netloc, target
