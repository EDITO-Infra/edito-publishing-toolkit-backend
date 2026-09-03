"""Tests for EPT's public /auth endpoint.

The route is public because users need it to exchange EDITO credentials or a
refresh token for bearer tokens before calling protected API routes.
"""

import asyncio

from ept.infrastructure.services.edito_auth import EditoBearerAuthError, EditoTokenResponse

from .helpers import create_api_shell_test_app, request
from tests.conftest import unit


@unit
def test_auth_endpoint_exchanges_username_password_for_tokens(monkeypatch):
    """The public auth route exchanges username/password credentials for tokens."""
    app = create_api_shell_test_app()
    calls: list[dict] = []

    async def fake_exchange(username: str, password: str) -> EditoTokenResponse:
        """Record the password grant inputs and return a stable token response."""
        calls.append({"username": username, "password": password})
        return EditoTokenResponse(
            access_token="access-secret",
            refresh_token="refresh-secret",
            token_type="bearer",
            expires_in=300,
            refresh_expires_in=1800,
        )

    monkeypatch.setattr("ept.api.auth.exchange_username_password", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            json={"grant_type": "password", "username": "alice", "password": "dashboard-secret"},
        )
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "token_type": "bearer",
        "expires_in": 300,
        "refresh_expires_in": 1800,
    }
    assert calls == [{"username": "alice", "password": "dashboard-secret"}]


@unit
def test_auth_endpoint_exchanges_refresh_token_for_new_tokens(monkeypatch):
    """The /auth route can be used to refresh an access token using a refresh token."""
    app = create_api_shell_test_app()
    calls: list[str] = []

    # simulate the exchange_refresh_token function to record calls and return a new token response
    async def fake_exchange(refresh_token: str) -> EditoTokenResponse:
        """Record the refresh token and return a stable refreshed token pair."""
        calls.append(refresh_token)
        # return a new token response to simulate a successful refresh
        return EditoTokenResponse(
            access_token="new-access",
            refresh_token="new-refresh",
            token_type="bearer",
            expires_in=300,
            refresh_expires_in=1800,
        )
    # make sure the monkeypatch is applied to the correct function in the auth module (uses exchange_refresh_token from ept.infrastructure.services.edito_auth)
    monkeypatch.setattr("ept.api.auth.exchange_refresh_token", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            json={"grant_type": "refresh_token", "refresh_token": "old-refresh"},
        )
    )

    assert response.status_code == 200
    # check that the response contains the new tokens
    assert response.json()["access_token"] == "new-access"
    assert response.json()["refresh_token"] == "new-refresh"
    assert calls == ["old-refresh"]


@unit
def test_auth_endpoint_rejects_ambiguous_grant():
    """The /auth route should reject requests that provide both password and refresh token."""
    app = create_api_shell_test_app()
    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            json={"username": "alice", "password": "secret", "refresh_token": "refresh"},
        )
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "https://api.edito-publishing-toolkit.org/problems/invalid-request"


@unit
def test_auth_endpoint_reports_503_when_auth_service_unavailable(monkeypatch):
    """A Keycloak outage during token exchange is reported as unavailable, not a 401."""
    app = create_api_shell_test_app()

    async def fake_exchange(_username: str, _password: str) -> EditoTokenResponse:
        """Simulate Keycloak being unreachable during a password-grant exchange."""
        raise EditoBearerAuthError(
            "EDITO authentication service is unavailable.", reason="auth_unavailable"
        )

    monkeypatch.setattr("ept.api.auth.exchange_username_password", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            json={"grant_type": "password", "username": "alice", "password": "secret"},
        )
    )

    assert response.status_code == 503
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/authentication-service-unavailable",
        "title": "Authentication service unavailable",
        "status": 503,
        "detail": "EDITO authentication service is unavailable.",
        "instance": "/v1/auth",
        "reason": "auth_unavailable",
    }


@unit
def test_auth_endpoint_hides_upstream_failure_details(monkeypatch):
    """Auth failures expose a safe reason without leaking upstream text."""
    app = create_api_shell_test_app()

    def fake_exchange(_username: str, _password: str) -> EditoTokenResponse:
        """Raise the same auth error shape the infrastructure helper would raise."""
        raise EditoBearerAuthError("upstream leaked detail", reason="auth_failed")

    monkeypatch.setattr("ept.api.auth.exchange_username_password", fake_exchange)

    response = asyncio.run(
        request(
            "POST",
            "/v1/auth",
            app=app,
            json={"grant_type": "password", "username": "alice", "password": "wrong-secret"},
        )
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/authentication-failed",
        "title": "Authentication failed",
        "status": 401,
        "detail": "EDITO authentication failed.",
        "instance": "/v1/auth",
        "reason": "auth_failed",
    }
    assert response.headers["www-authenticate"] == "Bearer"
