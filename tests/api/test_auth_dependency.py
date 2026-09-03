"""Tests proving protected API routes enforce bearer auth."""

import asyncio

from ept.infrastructure.services.edito_auth import EditoBearerAuthError, EditoBearerAuth

from .helpers import create_api_shell_test_app, request
from tests.conftest import unit


@unit
def test_protected_route_without_bearer_token_is_rejected():
    """Protected API routes reject requests without bearer auth."""
    app = create_api_shell_test_app()
    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            json={"value": "demo"},
        )
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/authentication-required",
        "title": "Authentication required",
        "status": 401,
        "detail": "Provide an Authorization header with a bearer access token.",
        "instance": "/v1/protected",
        "reason": "missing_bearer_token",
    }
    assert response.headers["www-authenticate"] == "Bearer"


@unit
def test_protected_route_with_malformed_auth_scheme_is_rejected():
    """Non-bearer authorization headers fail before request validation."""
    app = create_api_shell_test_app()
    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Basic abc123"},
            json={"value": "demo"},
        )
    )

    assert response.status_code == 401
    assert response.json()["reason"] == "missing_bearer_token"


@unit
def test_protected_route_with_invalid_bearer_token_is_rejected(monkeypatch):
    """Invalid bearer tokens return a specific safe reason."""
    app = create_api_shell_test_app()

    def fake_validate(_token: str) -> EditoBearerAuth:
        """Simulate infrastructure bearer validation rejecting the token."""
        raise EditoBearerAuthError("bad token", reason="invalid_bearer_token")

    monkeypatch.setattr("ept.api.dependencies.validate_bearer_token", fake_validate)

    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer bad-token"},
            json={"value": "demo"},
        )
    )

    assert response.status_code == 401
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/invalid-bearer-token",
        "title": "Invalid bearer token",
        "status": 401,
        "detail": "The bearer token is not valid.",
        "instance": "/v1/protected",
        "reason": "invalid_bearer_token",
    }


@unit
def test_protected_route_reports_503_when_auth_service_unavailable(monkeypatch):
    """A Keycloak/JWKS outage is reported as unavailable, not an invalid token."""
    app = create_api_shell_test_app()

    def fake_validate(_token: str) -> EditoBearerAuth:
        """Simulate the JWKS endpoint being unreachable."""
        raise EditoBearerAuthError(
            "EDITO authentication service is unavailable.", reason="auth_unavailable"
        )

    monkeypatch.setattr("ept.api.dependencies.validate_bearer_token", fake_validate)

    response = asyncio.run(
        request(
            "POST",
            "/v1/protected",
            app=app,
            headers={"Authorization": "Bearer some-token"},
            json={"value": "demo"},
        )
    )

    assert response.status_code == 503
    assert response.json() == {
        "type": "https://api.edito-publishing-toolkit.org/problems/authentication-service-unavailable",
        "title": "Authentication service unavailable",
        "status": 503,
        "detail": "EDITO authentication service is unavailable.",
        "instance": "/v1/protected",
        "reason": "auth_unavailable",
    }


@unit
def test_protected_route_with_valid_bearer_token_reaches_request_validation(monkeypatch):
    """A valid bearer token gets past auth, then the API validates the body."""
    app = create_api_shell_test_app()
    calls: list[str] = []

    def fake_validate(token: str) -> EditoBearerAuth:
        """Record the bearer token and return a stable validated principal."""
        calls.append(token)
        return EditoBearerAuth(subject="test-user", username="alice", claims={"sub": "test-user"}, access_token=token)

    monkeypatch.setattr("ept.api.dependencies.validate_bearer_token", fake_validate)

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
    assert calls == ["valid-token"]
