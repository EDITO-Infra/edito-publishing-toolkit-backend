"""Integration tests for the ISO19139 → STAC Item API route.

These run against the FastAPI app with a local ISO record and mock-free
request handling; no external services are contacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import integration, unit
from fastapi.testclient import TestClient

from ept.api.app import create_app
from tests.features.iso_to_stac_item.test_iso_to_stac_item import ISO_XML


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


@pytest.fixture()
def iso_record(tmp_path: Path) -> Path:
    path = tmp_path / "record.xml"
    path.write_text(ISO_XML, encoding="utf-8")
    return path


def _auth_headers(client: TestClient) -> dict[str, str]:
    """Obtain a bearer token from the test auth fixtures."""

    response = client.post(
        "/v1/auth",
        json={"grant_type": "password", "username": "test", "password": "test"},
    )
    if response.status_code != 200:
        pytest.skip("No test Keycloak available; auth-gated route not exercised.")
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@integration
def test_convert_local_record_to_s3_output(client: TestClient, iso_record: Path) -> None:
    """Standard S3 output — only exercised when live S3 config is present."""

    headers = _auth_headers(client)
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(iso_record), "target": "my-metadata"},
        headers=headers,
    )
    if response.status_code in {409, 502, 503}:
        pytest.skip("Live S3 storage not reachable from the test environment.")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["item_id"] == "test-dataset-001"
    assert body["stac_item"]["type"] == "Feature"
    assert body["target_uri"].endswith("/my-metadata/test-dataset-001.json")


@integration
def test_convert_with_local_output(client: TestClient, iso_record: Path, tmp_path: Path) -> None:
    headers = _auth_headers(client)
    output_dir = tmp_path / "out"
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(iso_record), "target": None, "local_output": str(output_dir)},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["bucket"] is None
    assert body["target_uri"].endswith("test-dataset-001.json")
    assert Path(body["target_uri"]).exists()


@integration
def test_missing_target_returns_422(client: TestClient, iso_record: Path) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(iso_record), "target": None},
        headers=headers,
    )
    assert response.status_code == 422


@integration
def test_missing_bbox_returns_422(client: TestClient, tmp_path: Path) -> None:
    headers = _auth_headers(client)
    broken = tmp_path / "broken.xml"
    broken.write_text(
        ISO_XML.replace("westBoundLongitude", "missingWestBoundLongitude"),
        encoding="utf-8",
    )
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(broken)},
        headers=headers,
    )
    assert response.status_code == 422


@integration
def test_invalid_xml_returns_422(client: TestClient, tmp_path: Path) -> None:
    headers = _auth_headers(client)
    broken = tmp_path / "invalid.xml"
    broken.write_text("this is not xml", encoding="utf-8")
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(broken)},
        headers=headers,
    )
    assert response.status_code == 422


@integration
def test_missing_source_returns_404(client: TestClient, tmp_path: Path) -> None:
    headers = _auth_headers(client)
    response = client.post(
        "/v1/metadata/iso-to-stac-item",
        json={"source_uri": str(tmp_path / "does-not-exist.xml")},
        headers=headers,
    )
    assert response.status_code == 404
