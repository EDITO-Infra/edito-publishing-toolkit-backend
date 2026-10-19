"""Integration tests for the stac_get feature (live services)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ept.api.main import app
from tests.conftest import integration, live_edito_token_pair  # noqa: F401


@integration
def test_get_route_is_mounted_and_authenticated():
    """The registry mounts the route; unauthenticated requests get 401/403."""
    with TestClient(app) as client:
        response = client.get("/v1/stac/get", params={"url": "https://example.com/catalog.json"})
        assert response.status_code in (401, 403)


@integration
def test_get_route_fetches_live_edito_catalog(live_edito_token_pair):  # noqa: F811
    """Fetch the EDITO STAC API root with a live token (when env is configured)."""
    import os

    from tests.conftest import require_live_env

    base = os.getenv("EDITO_STAC_API_URL", "https://api.dive.edito.eu/data").strip().strip("\"'").rstrip("/")
    with TestClient(app) as client:
        response = client.get(
            "/v1/stac/get",
            params={"url": f"{base}/"},
            headers={"Authorization": f"Bearer {live_edito_token_pair.access_token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["object_type"] in {"catalog", "unknown"}
    assert body["stac_object"].get("id")
