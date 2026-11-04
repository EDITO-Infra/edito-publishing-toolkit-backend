"""Tests for instance-to-owner binding (first-login lock)."""

import asyncio
import base64
import json

from ept.infrastructure.services.edito_auth import (
    EditoAuthResponse,
    EditoBearerAuth,
    EditoTokenResponse,
    S3Credentials,
)

from .helpers import create_api_shell_test_app, request
from tests.conftest import unit


def _fake_validate(monkeypatch, subject: str, username: str) -> None:
    """Make bearer validation return a stable principal for the given subject."""

    def fake_validate(token: str) -> EditoBearerAuth:
        return EditoBearerAuth(
            subject=subject, username=username, claims={"sub": subject}, access_token=token
        )

    monkeypatch.setattr("ept.api.dependencies.validate_bearer_token", fake_validate)


def _token_for(subject: str, username: str) -> str:
    """Build an unsigned JWT the binding module can read without verification."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": subject, "preferred_username": username}).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.c2ln"


@unit
def test_first_authenticated_user_claims_instance(monkeypatch, tmp_path):
    """The first valid bearer token claims the instance and passes through."""
    app = create_api_shell_test_app()
    _fake_validate(monkeypatch, "alice-sub", "alice")

    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer valid-token"},
            json={},
        )
    )

    assert response.status_code == 422  # past auth, failed body validation
    state = tmp_path / "owner.state"
    assert state.exists()
    record = json.loads(state.read_text(encoding="utf-8"))
    assert record["username"] == "alice"
    assert "sub_sha256" in record


@unit
def test_second_user_is_rejected_with_403(monkeypatch):
    """A different subject after binding is rejected with instance_bound."""
    app = create_api_shell_test_app()
    _fake_validate(monkeypatch, "alice-sub", "alice")
    asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer alice-token"},
            json={"value": "demo"},
        )
    )

    _fake_validate(monkeypatch, "bob-sub", "bob")
    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer bob-token"},
            json={"value": "demo"},
        )
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/deployment-bound",
        "title": "Deployment bound to another user",
        "status": 403,
        "detail": "This deployment is bound to another EDITO user.",
        "instance": "/v1/protected",
        "reason": "instance_bound",
    }


@unit
def test_corrupt_state_file_is_treated_as_unclaimed(monkeypatch, tmp_path):
    """A malformed state file must not lock the deployment permanently."""
    state = tmp_path / "owner.state"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not-json", encoding="utf-8")
    app = create_api_shell_test_app()
    _fake_validate(monkeypatch, "alice-sub", "alice")

    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer valid-token"},
            json={},
        )
    )

    assert response.status_code == 422
    assert json.loads(state.read_text(encoding="utf-8"))["username"] == "alice"


@unit
def test_password_grant_claims_instance(monkeypatch, tmp_path):
    """A successful password login binds the instance to that subject."""
    app = create_api_shell_test_app()

    async def fake_exchange(username: str, password: str) -> EditoAuthResponse:
        return EditoAuthResponse(
            access_token=_token_for("alice-sub", "alice"),
            refresh_token="refresh-secret",
            token_type="bearer",
            expires_in=300,
            refresh_expires_in=1800,
            s3_credentials=S3Credentials(
                endpoint_url="https://s3.example.test",
                access_key_id="k",
                secret_access_key="s",
                session_token="t",
            ),
        )

    monkeypatch.setattr("ept.api.auth.exchange_auth_credentials", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            data={"username": "alice", "password": "secret"},
        )
    )

    assert response.status_code == 200
    record = json.loads((tmp_path / "owner.state").read_text(encoding="utf-8"))
    assert record["username"] == "alice"


@unit
def test_refresh_grant_from_other_user_is_rejected(monkeypatch):
    """A refresh token from a different subject cannot use a bound instance."""
    app = create_api_shell_test_app()
    _fake_validate(monkeypatch, "alice-sub", "alice")
    asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer alice-token"},
            json={"value": "demo"},
        )
    )

    async def fake_exchange(refresh_token: str) -> EditoTokenResponse:
        return EditoTokenResponse(
            access_token=_token_for("mallory-sub", "mallory"),
            refresh_token="new-refresh",
            token_type="bearer",
            expires_in=300,
            refresh_expires_in=1800,
        )

    monkeypatch.setattr("ept.api.auth.exchange_refresh_token", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            data={"refresh_token": "stolen-refresh"},
        )
    )

    assert response.status_code == 403
    assert response.json()["reason"] == "instance_bound"
