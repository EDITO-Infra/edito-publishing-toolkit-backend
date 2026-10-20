"""Integration test for the STAC Item builder route (mounted + authenticated)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ept.api.main import app
from tests.conftest import integration


@integration
def test_build_item_route_is_mounted_and_authenticated():
    with TestClient(app) as client:
        response = client.post("/v1/stac/build/item", json={"id": "x"})
        assert response.status_code in (401, 403)
