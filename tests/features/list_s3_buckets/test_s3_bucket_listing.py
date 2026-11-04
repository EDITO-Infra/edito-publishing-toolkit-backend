"""Tests for the list-s3-buckets service use case."""

from __future__ import annotations

from typing import Any

import pytest

from ept.features.list_s3_buckets.models import ListS3BucketsRequest
from ept.features.list_s3_buckets.service import list_s3_buckets
from ept.infrastructure.services.edito_auth import S3ConnectionParameters
from tests.conftest import unit


class FakeS3FileSystem:
    """Records call_s3 invocations and answers with one canned payload."""

    def __init__(self, buckets: list[dict[str, Any]]) -> None:
        self._buckets = buckets
        self.calls: list[tuple[str, tuple, dict]] = []

    def call_s3(self, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((method, args, kwargs))
        return {"Buckets": self._buckets, "Owner": {"DisplayName": "alice"}}


@unit
def test_list_s3_buckets_returns_sorted_bucket_names(monkeypatch):
    fake = FakeS3FileSystem([{"Name": "project-demo"}, {"Name": "oidc-alice"}])
    monkeypatch.setattr(
        "ept.features.list_s3_buckets.service.s3_url_to_fs",
        lambda uri, *, storage_options: (fake, "/"),
    )

    buckets = list_s3_buckets(_connection())

    assert buckets == ["oidc-alice", "project-demo"]
    assert fake.calls == [("list_buckets", (), {})]


@unit
def test_list_s3_buckets_without_visible_buckets_returns_empty_list(monkeypatch):
    fake = FakeS3FileSystem([])
    monkeypatch.setattr(
        "ept.features.list_s3_buckets.service.s3_url_to_fs",
        lambda uri, *, storage_options: (fake, "/"),
    )

    assert list_s3_buckets(_connection()) == []


@unit
def test_list_request_reuses_connection_parameters_contract():
    schema = ListS3BucketsRequest.model_json_schema()

    assert {"endpoint_url", "access_key_id", "secret_access_key"} <= set(schema["required"])
    # Static-key S3 connections work without a session token.
    assert "session_token" not in schema["required"]
    assert "bucket" not in schema["properties"]


def _connection() -> S3ConnectionParameters:
    return S3ConnectionParameters(
        endpoint_url="https://s3.example.test",
        access_key_id="temporary-key",
        secret_access_key="temporary-secret",
        session_token="temporary-token",
    )
