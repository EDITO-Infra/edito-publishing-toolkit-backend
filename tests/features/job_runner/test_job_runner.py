"""Tests for the EPT job runner (manager lifecycle + route behavior)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ept.api.dependencies import require_edito_bearer_auth
from ept.api.main import app
from ept.features.job_runner.manager import FeatureRun, RunManager
from ept.features.job_runner.operations import OPERATIONS, RunOperation
from ept.infrastructure.services.edito_auth import EditoBearerAuth
from tests.conftest import feature_is_active, integration, unit

pytestmark = pytest.mark.skipif(
    not feature_is_active("jobs.runner"),
    reason="feature is not mounted in the active release",
)


# ── Manager lifecycle (direct, no HTTP) ──────────────────────────────────


async def _await_run(run: FeatureRun) -> None:
    assert run.task is not None
    await asyncio.wait({run.task}, timeout=5)
    assert run.task.done()


@unit
def test_run_succeeds_and_stores_result():
    async def scenario():
        async def runner(payload, *, access_token):
            return {"echo": payload["value"], "token_ignored": access_token is not None}

        manager = RunManager()
        run = manager.submit(
            operation="test.echo",
            runner=runner,
            payload={"value": 41},
            access_token="tok",
            owner="alice",
        )
        await _await_run(run)
        assert run.status == "succeeded"
        assert run.result == {"echo": 41, "token_ignored": True}
        assert run.error is None
        assert run.started_at is not None and run.finished_at is not None

    asyncio.run(scenario())


@unit
def test_run_failure_captures_message():
    async def scenario():
        async def runner(payload, *, access_token):
            raise ValueError("boom")

        manager = RunManager()
        run = manager.submit(
            operation="test.fail",
            runner=runner,
            payload={},
            access_token="tok",
            owner="alice",
        )
        await _await_run(run)
        assert run.status == "failed"
        assert run.error == "boom"
        assert run.result is None

    asyncio.run(scenario())


@unit
def test_cancel_queued_run():
    async def scenario():
        async def runner(payload, *, access_token):  # pragma: no cover - never runs
            return {}

        manager = RunManager()
        run = manager.submit(
            operation="test.slow",
            runner=runner,
            payload={},
            access_token="tok",
            owner="alice",
        )
        # Cancelled before the loop ever started the task.
        await manager.cancel(run)
        assert run.status == "cancelled"
        assert run.finished_at is not None

    asyncio.run(scenario())


@unit
def test_cancel_running_run():
    async def scenario():
        release = asyncio.Event()

        async def runner(payload, *, access_token):
            await release.wait()
            return {}

        manager = RunManager()
        run = manager.submit(
            operation="test.slow",
            runner=runner,
            payload={},
            access_token="tok",
            owner="alice",
        )
        await asyncio.sleep(0)  # let the task start
        assert run.status == "running"
        release.set()
        await manager.cancel(run)
        assert run.status == "cancelled"
        await _await_run(run)

    asyncio.run(scenario())


@unit
def test_prune_caps_terminal_runs(monkeypatch):
    async def scenario():
        from ept.features.job_runner import manager as manager_module

        monkeypatch.setattr(manager_module, "MAX_TERMINAL_RUNS", 2)

        async def runner(payload, *, access_token):
            return {}

        manager = RunManager()
        for _ in range(4):
            run = manager.submit(
                operation="test.echo",
                runner=runner,
                payload={},
                access_token="tok",
                owner="alice",
            )
            await _await_run(run)
        terminal = [r for r in manager._runs.values() if r.status == "succeeded"]
        assert len(terminal) <= 2

    asyncio.run(scenario())


# ── Route behavior (TestClient, fake operations, no network) ─────────────


class EchoRequest(BaseModel):
    value: int


async def _echo_service(request: EchoRequest, *, access_token: str | None = None) -> dict[str, Any]:
    await asyncio.sleep(0)
    return {"value": request.value}


async def _slow_service(request: EchoRequest, *, access_token: str | None = None) -> dict[str, Any]:
    await asyncio.sleep(30)
    return {}  # pragma: no cover - cancelled before completion


@pytest.fixture
def client():
    holder: dict[str, EditoBearerAuth] = {
        "auth": EditoBearerAuth(subject="sub-1", username="alice", claims={}, access_token="tok")
    }
    OPERATIONS["test.echo"] = RunOperation(request_model=EchoRequest, service=_echo_service, key="test.echo")
    OPERATIONS["test.slow"] = RunOperation(request_model=EchoRequest, service=_slow_service, key="test.slow")
    app.dependency_overrides[require_edito_bearer_auth] = lambda: holder["auth"]
    with TestClient(app) as test_client:
        yield test_client, holder
    app.dependency_overrides.pop(require_edito_bearer_auth, None)
    for key in ("test.echo", "test.slow"):
        OPERATIONS.pop(key, None)
    from ept.features.job_runner.manager import run_manager

    run_manager._runs.clear()


def _poll_until_terminal(client: TestClient, run_id: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"succeeded", "failed", "cancelled"}:
            return body
        time.sleep(0.01)
    pytest.fail("run did not reach a terminal state in time")


@integration
def test_submit_poll_happy_path(client):
    test_client, _ = client
    response = test_client.post("/v1/runs", json={"operation": "test.echo", "request": {"value": 41}})
    assert response.status_code == 202
    body = response.json()
    assert body["operation"] == "test.echo"
    assert body["status"] == "queued"
    assert body["run_id"].startswith("run-")
    assert body["poll_url"] == f"/v1/runs/{body['run_id']}"

    final = _poll_until_terminal(test_client, body["run_id"])
    assert final["status"] == "succeeded"
    assert final["result"] == {"value": 41}
    assert "tok" not in response.text


@integration
def test_unknown_operation_rejected(client):
    test_client, _ = client
    response = test_client.post("/v1/runs", json={"operation": "nope", "request": {}})
    assert response.status_code == 400
    assert response.json()["reason"] == "unknown_operation"


@integration
def test_invalid_payload_rejected(client):
    test_client, _ = client
    response = test_client.post(
        "/v1/runs", json={"operation": "test.echo", "request": {"value": "not-an-int"}}
    )
    assert response.status_code == 422
    assert response.json()["reason"] == "invalid_run_request"


@integration
def test_run_owner_isolation(client):
    test_client, holder = client
    submitted = test_client.post(
        "/v1/runs", json={"operation": "test.echo", "request": {"value": 1}}
    ).json()
    holder["auth"] = EditoBearerAuth(subject="sub-2", username="bob", claims={}, access_token="tok")
    assert test_client.get(f"/v1/runs/{submitted['run_id']}").status_code == 404
    assert test_client.delete(f"/v1/runs/{submitted['run_id']}").status_code == 404
    assert test_client.get("/v1/runs/run-does-not-exist").status_code == 404


@integration
def test_cancel_running_run_via_route(client):
    test_client, _ = client
    submitted = test_client.post(
        "/v1/runs", json={"operation": "test.slow", "request": {"value": 1}}
    ).json()
    response = test_client.delete(f"/v1/runs/{submitted['run_id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@integration
def test_list_operations_exposes_endpoint(client):
    """The registry lists declared operations with their synchronous endpoint."""
    test_client, _ = client
    # Also proves route ordering: /v1/runs/operations must not be captured by
    # GET /v1/runs/{run_id} as run_id='operations'.
    response = test_client.get("/v1/runs/operations")
    assert response.status_code == 200
    operations = {op["key"]: op for op in response.json()["operations"]}
    assert operations["stac.validate"]["endpoint"] == "POST /v1/stac/validate"


@integration
def test_list_runs_is_owner_scoped_and_excludes_result(client):
    """GET /v1/runs lists only the caller's runs, without result payloads."""
    test_client, holder = client
    submitted = test_client.post(
        "/v1/runs", json={"operation": "test.echo", "request": {"value": 41}}
    ).json()

    mine = test_client.get("/v1/runs")
    assert mine.status_code == 200
    runs = mine.json()["runs"]
    assert [run["run_id"] for run in runs] == [submitted["run_id"]]
    assert all("result" not in run for run in runs)

    holder["auth"] = EditoBearerAuth(subject="sub-2", username="bob", claims={}, access_token="tok")
    foreign = test_client.get("/v1/runs")
    assert foreign.status_code == 200
    assert foreign.json()["runs"] == []
