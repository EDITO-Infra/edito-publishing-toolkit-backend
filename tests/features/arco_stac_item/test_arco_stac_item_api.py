"""Integration test for the ARCO STAC Item route (mounted + authenticated)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ept.api.main import app
from ept.features.arco_stac_item.models import ArcoStacItemRequest
from tests.conftest import integration


@integration
def test_arco_route_is_mounted_and_authenticated():
    with TestClient(app) as client:
        response = client.post("/v1/arco/stac-item", json={"source_uri": "x", "target": "y"})
        assert response.status_code in (401, 403)


@integration
def test_arco_operation_is_registered_in_the_job_runner():
    from ept.features.job_runner.operations import OPERATIONS

    operation = OPERATIONS["arco.stac-item"]
    assert operation.endpoint == "POST /v1/arco/stac-item"
    assert operation.request_model is ArcoStacItemRequest
