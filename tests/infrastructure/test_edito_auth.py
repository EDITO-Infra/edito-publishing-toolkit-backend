"""Tests for the shared EDITO Keycloak authentication helpers.

The first group is unit-level and monkeypatches HTTP/JWT dependencies so the
expected request and validation behavior is deterministic. The tests marked
``integration`` use real EDITO services when live credentials are configured.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time

import httpx
import jwt
import pytest
from pydantic import TypeAdapter, ValidationError

from ept.infrastructure.services.edito_auth import (
    DEFAULT_EDITO_AUTH_URL,
    EditoBearerAuthError,
    EditoPasswordTokenRequest,
    EditoRefreshTokenRequest,
    EditoTokenRequest,
    exchange_refresh_token,
    exchange_username_password,
    validate_bearer_token,
)
from tests.conftest import integration, require_live_env, unit


@unit
def test_default_auth_endpoint_uses_dive():
    """Unconfigured auth exchanges and validation must target EDITO DIVE."""
    assert DEFAULT_EDITO_AUTH_URL == (
        "https://auth.dive.edito.eu/auth/realms/datalab/protocol/openid-connect/token"
    )


@unit
def test_username_password_exchange_sends_password_grant(monkeypatch):
    """Password login must send the Keycloak password-grant form fields."""
    calls: list[dict] = []

    async def fake_post(self: httpx.AsyncClient, url: str, *, data: dict, follow_redirects: bool) -> httpx.Response:
        """Record the password-grant HTTP form and return token JSON."""
        calls.append({"url": url, "data": data, "follow_redirects": follow_redirects})
        return httpx.Response(
            200,
            json={"access_token": "access-secret", "refresh_token": "refresh-secret"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setenv("EDITO_AUTH_URL", "https://auth.test/token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    token = asyncio.run(exchange_username_password("alice", "dashboard-secret"))

    assert token.access_token == "access-secret"
    assert token.refresh_token == "refresh-secret"
    assert calls == [
        {
            "url": "https://auth.test/token",
            "data": {
                "grant_type": "password",
                "client_id": "edito",
                "username": "alice",
                "password": "dashboard-secret",
                "scope": "openid offline_access",
            },
            "follow_redirects": True,
        }
    ]


@unit
def test_explicit_credentials_are_exchanged_for_full_token_response(monkeypatch):
    """Token responses are normalized into the public Pydantic response model."""
    async def fake_post(self: httpx.AsyncClient, url: str, *, data: dict, follow_redirects: bool) -> httpx.Response:
        """Return a complete token response with string and integer lifetimes."""
        return httpx.Response(
            200,
            json={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "token_type": "Bearer",
                "expires_in": "300",
                "refresh_expires_in": 1800,
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setenv("EDITO_AUTH_URL", "https://auth.test/token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    token = asyncio.run(exchange_username_password("alice", "dashboard-secret"))

    assert token.access_token == "access-secret"
    assert token.refresh_token == "refresh-secret"
    assert token.token_type == "bearer"
    assert token.expires_in == 300
    assert token.refresh_expires_in == 1800


@unit
def test_token_exchange_logs_safe_diagnostics(caplog, monkeypatch):
    """Auth logs describe token receipt without exposing credentials."""
    async def fake_post(self: httpx.AsyncClient, url: str, *, data: dict, follow_redirects: bool) -> httpx.Response:
        """Return a successful token response for log assertions."""
        return httpx.Response(
            200,
            json={"access_token": "access-secret", "refresh_token": "refresh-secret"},
            request=httpx.Request("POST", url),
        )

    caplog.set_level(logging.INFO, logger="ept.infrastructure.services.edito_auth")
    monkeypatch.delenv("EPT_LOG_TOKEN_VALUES", raising=False)
    monkeypatch.setenv("EDITO_AUTH_URL", "https://auth.test/token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    asyncio.run(exchange_username_password("alice", "dashboard-secret"))

    assert "grant_type=password" in caplog.text
    assert "access-secret" not in caplog.text
    assert "refresh-secret" not in caplog.text
    assert "alice" not in caplog.text
    assert "dashboard-secret" not in caplog.text


@unit
def test_refresh_token_exchange_sends_refresh_grant(monkeypatch):
    """Refresh login must send the Keycloak refresh-token grant fields."""
    calls: list[dict] = []

    async def fake_post(self: httpx.AsyncClient, url: str, *, data: dict, follow_redirects: bool) -> httpx.Response:
        """Record the refresh-grant HTTP form and return refreshed token JSON."""
        calls.append({"url": url, "data": data, "follow_redirects": follow_redirects})
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setenv("EDITO_AUTH_URL", "https://auth.test/token")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    token = asyncio.run(exchange_refresh_token("old-refresh"))

    assert token.access_token == "new-access"
    assert token.refresh_token == "new-refresh"
    assert calls == [
        {
            "url": "https://auth.test/token",
            "data": {
                "grant_type": "refresh_token",
                "client_id": "edito",
                "refresh_token": "old-refresh",
                "scope": "openid offline_access",
            },
            "follow_redirects": True,
        }
    ]


@unit
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"grant_type": "bogus"},
        {"username": "alice", "password": "dashboard-secret"},
        {"grant_type": "password", "username": "alice"},
        {"grant_type": "password", "password": "dashboard-secret"},
        {"grant_type": "refresh_token"},
        {
            "grant_type": "password",
            "username": "alice",
            "password": "dashboard-secret",
            "refresh_token": "old-refresh",
        },
    ],
)
def test_token_request_accepts_exactly_one_grant_shape(payload):
    """Token requests need a valid grant_type plus that grant's complete, exact fields."""
    with pytest.raises(ValidationError):
        TypeAdapter(EditoTokenRequest).validate_python(payload)


@unit
def test_token_request_schema_documents_grant_alternatives():
    """The token request schema should document the supported grant alternatives."""
    schema = TypeAdapter(EditoTokenRequest).json_schema()

    assert schema["oneOf"] == [
        {"$ref": "#/$defs/EditoPasswordTokenRequest"},
        {"$ref": "#/$defs/EditoRefreshTokenRequest"},
    ]
    assert schema["discriminator"] == {
        "propertyName": "grant_type",
        "mapping": {
            "password": "#/$defs/EditoPasswordTokenRequest",
            "refresh_token": "#/$defs/EditoRefreshTokenRequest",
        },
    }
    assert schema["$defs"]["EditoPasswordTokenRequest"]["required"] == [
        "grant_type",
        "username",
        "password",
    ]
    assert schema["$defs"]["EditoRefreshTokenRequest"]["required"] == ["grant_type", "refresh_token"]
    assert TypeAdapter(EditoTokenRequest).validate_python(
        {"grant_type": "password", "username": "alice", "password": "secret"}
    ) == EditoPasswordTokenRequest(grant_type="password", username="alice", password="secret")
    assert TypeAdapter(EditoTokenRequest).validate_python(
        {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
    ) == EditoRefreshTokenRequest(grant_type="refresh_token", refresh_token="old-refresh")


@unit
def test_bearer_token_is_validated_against_keycloak_claims(monkeypatch):
    """Bearer validation checks Keycloak issuer, signature lookup, and client."""
    class FakeSigningKey:
        """Minimal signing key object returned by the fake JWKS client."""

        key = "public-key"

    class FakeJwksClient:
        """Fake JWKS client that records the Keycloak certs URL."""

        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
            """Return the fake signing key after asserting the token and URL."""
            assert token == "valid-token"
            assert self.url == "https://auth.test/auth/realms/datalab/protocol/openid-connect/certs"
            return FakeSigningKey()

    def fake_decode(token: str, key: str, *, algorithms: list[str], issuer: str, options: dict) -> dict:
        """Return validated claims after asserting JWT decode inputs."""
        assert token == "valid-token"
        assert key == "public-key"
        assert algorithms == ["RS256", "RS512"]
        assert issuer == "https://auth.test/auth/realms/datalab"
        assert options["verify_aud"] is False
        return {
            "sub": "subject-1",
            "preferred_username": "alice",
            "azp": "edito",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }

    monkeypatch.setenv(
        "EDITO_AUTH_URL",
        "https://auth.test/auth/realms/datalab/protocol/openid-connect/token",
    )
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    monkeypatch.setattr(jwt, "decode", fake_decode)

    principal = validate_bearer_token("valid-token")

    assert principal.subject == "subject-1"
    assert principal.username == "alice"
    assert principal.access_token == "valid-token"


@unit
def test_bearer_validation_derives_issuer_from_configured_token_endpoint(monkeypatch):
    """Validation derives the issuer from the configured Keycloak token URL."""
    class FakeSigningKey:
        """Minimal signing key object returned by the fake JWKS client."""

        key = "public-key"

    class FakeJwksClient:
        """Fake JWKS client for the issuer derived from the token URL."""

        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, token: str) -> FakeSigningKey:
            """Return the fake key after asserting the derived JWKS URL."""
            assert token == "valid-token"
            assert self.url == "https://auth.configured.test/auth/realms/datalab/protocol/openid-connect/certs"
            return FakeSigningKey()

    def fake_decode(token: str, key: str, *, algorithms: list[str], issuer: str, options: dict) -> dict:
        """Return claims after asserting the issuer derived from the token URL."""
        assert token == "valid-token"
        assert key == "public-key"
        assert issuer == "https://auth.configured.test/auth/realms/datalab"
        return {
            "sub": "subject-1",
            "preferred_username": "alice",
            "azp": "edito",
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
        }

    monkeypatch.setenv(
        "EDITO_AUTH_URL",
        "https://auth.configured.test/auth/realms/datalab/protocol/openid-connect/token",
    )
    monkeypatch.delenv("EDITO_AUTH_ISSUER_URL", raising=False)
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    monkeypatch.setattr(jwt, "decode", fake_decode)

    principal = validate_bearer_token("valid-token")

    assert principal.subject == "subject-1"
    assert principal.username == "alice"


@unit
def test_bearer_validation_can_use_explicit_issuer_override(monkeypatch):
    """Operators can set EDITO_AUTH_ISSUER_URL when token and endpoint hosts differ."""
    class FakeSigningKey:
        """Minimal signing key object returned by the fake JWKS client."""

        key = "public-key"

    class FakeJwksClient:
        """Fake JWKS client for the explicit issuer override."""

        def __init__(self, url: str) -> None:
            self.url = url

        def get_signing_key_from_jwt(self, _token: str) -> FakeSigningKey:
            """Return the fake signing key after asserting override URL usage."""
            assert self.url == "https://issuer.test/auth/realms/datalab/protocol/openid-connect/certs"
            return FakeSigningKey()

    monkeypatch.setenv(
        "EDITO_AUTH_URL",
        "https://token-endpoint.test/auth/realms/datalab/protocol/openid-connect/token",
    )
    monkeypatch.setenv("EDITO_AUTH_ISSUER_URL", "https://issuer.test/auth/realms/datalab/")
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    monkeypatch.setattr(
        jwt,
        "decode",
        lambda *_args, **_kwargs: {
            "sub": "subject-1",
            "preferred_username": "alice",
            "azp": "edito",
        },
    )

    principal = validate_bearer_token("valid-token")

    assert principal.subject == "subject-1"


@unit
def test_bearer_token_rejects_unexpected_client(monkeypatch):
    """A correctly signed token is still rejected when it targets another client."""
    class FakeSigningKey:
        """Minimal signing key object returned by the fake JWKS client."""

        key = "public-key"

    class FakeJwksClient:
        """Fake JWKS client for client-mismatch validation."""

        def __init__(self, _url: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _token: str) -> FakeSigningKey:
            """Return the fake signing key without external network access."""
            return FakeSigningKey()

    monkeypatch.setenv(
        "EDITO_AUTH_URL",
        "https://auth.test/auth/realms/datalab/protocol/openid-connect/token",
    )
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    monkeypatch.setattr(
        jwt,
        "decode",
        lambda *_args, **_kwargs: {"sub": "subject-1", "azp": "other-client"},
    )

    with pytest.raises(EditoBearerAuthError):
        validate_bearer_token("wrong-client-token")


@unit
def test_bearer_validation_reports_auth_unavailable_when_jwks_unreachable(monkeypatch):
    """A JWKS fetch failure is a distinct 'unavailable' reason, not 'invalid token'."""
    class UnreachableJwksClient:
        """Fake JWKS client simulating a Keycloak/network outage."""

        def __init__(self, _url: str) -> None:
            pass

        def get_signing_key_from_jwt(self, _token: str) -> None:
            """Raise the same connection error PyJWKClient raises on a failed fetch."""
            raise jwt.PyJWKClientConnectionError("could not reach jwks endpoint")

    monkeypatch.setenv(
        "EDITO_AUTH_URL",
        "https://auth.unreachable.test/auth/realms/datalab/protocol/openid-connect/token",
    )
    monkeypatch.setattr(jwt, "PyJWKClient", UnreachableJwksClient)

    with pytest.raises(EditoBearerAuthError) as exc_info:
        validate_bearer_token("any-token")

    assert exc_info.value.reason == "auth_unavailable"


@integration
def test_live_edito_username_password_exchange(live_edito_credentials):
    """Exchange real EDITO credentials and validate the returned token pair."""
    token = asyncio.run(
        exchange_username_password(
            live_edito_credentials.username,
            live_edito_credentials.password,
        )
    )

    _assert_live_token_response(token.access_token, token.refresh_token)


@integration
def test_live_edito_refresh_token_exchange(live_edito_credentials):
    """Obtain a fresh pair, refresh it, and validate the rotated token pair."""
    initial = asyncio.run(
        exchange_username_password(
            live_edito_credentials.username,
            live_edito_credentials.password,
        )
    )
    refreshed = asyncio.run(exchange_refresh_token(initial.refresh_token))

    _assert_live_token_response(
        refreshed.access_token,
        refreshed.refresh_token,
    )


@integration
def test_live_edito_access_token_is_accepted_by_stac_api(
    live_edito_token_pair,
):
    """Require the issued token and username claim to work with EDITO STAC."""
    stac_api = require_live_env("EDITO_STAC_API")
    auth_url = os.getenv("EDITO_AUTH_URL", DEFAULT_EDITO_AUTH_URL)

    _assert_token_is_accepted_by_stac_api(
        live_edito_token_pair.access_token,
        stac_api,
        auth_url,
    )


def _username_from_access_token(token: str) -> str:
    """Return the username required by EDITO STAC and publishing services."""
    _header, payload, _signature = token.split(".")
    claims = _decode_jwt_segment(payload)
    username = claims.get("preferred_username")
    if not isinstance(username, str) or not username:
        pytest.fail(
            "EDITO access token does not include preferred_username.",
            pytrace=False,
        )
    return username


def _assert_jwt_access_token(token: str) -> None:
    """Check only stable JWT claims; signature validation is tested elsewhere."""
    header, payload, signature = token.split(".")
    assert header
    assert signature

    claims = _decode_jwt_segment(payload)
    assert claims["typ"] == "Bearer"
    assert claims["preferred_username"]
    assert claims["exp"] > int(time.time())


def _assert_live_token_response(access_token: str, refresh_token: str) -> None:
    """Check token exchange output without exposing token values."""
    _assert_jwt_access_token(access_token)
    assert refresh_token
    assert refresh_token != access_token


def _assert_token_is_accepted_by_stac_api(
    access_token: str,
    stac_api: str,
    auth_url: str,
) -> None:
    """Call the live EDITO STAC user-catalog route with the issued token."""
    username = _username_from_access_token(access_token)
    response = httpx.get(
        f"{stac_api.rstrip('/')}/users/{username}/catalogs",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30.0,
        follow_redirects=True,
    )
    assert response.status_code == 200, (
        "Access token from EDITO_AUTH_URL was not accepted by EDITO_STAC_API: "
        f"auth_url={auth_url}, stac_api={stac_api}, "
        f"status={response.status_code}, body={response.text}"
    )


def _decode_jwt_segment(segment: str) -> dict:
    """Decode one base64url JWT segment without verifying the token."""
    padding = "=" * (-len(segment) % 4)
    decoded = base64.urlsafe_b64decode(f"{segment}{padding}")
    payload = json.loads(decoded)
    assert isinstance(payload, dict)
    return payload
