"""EDITO Keycloak token exchange and bearer-token validation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging
import os
from typing import Annotated, Any, Literal

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from ept.infrastructure.utils.logging import token_diagnostic

logger = logging.getLogger(__name__)

DEFAULT_EDITO_AUTH_URL = (
    "https://auth.dive.edito.eu/auth/realms/datalab/protocol/openid-connect/token"
)
DEFAULT_EDITO_CLIENT_ID = "edito"
DEFAULT_EDITO_SCOPE = "openid offline_access"

# Keep the two OAuth grants as separate models, discriminated by `grant_type`
# (matching EDITO's Keycloak token endpoint). They have different required
# fields, so modelling them separately preserves an accurate OpenAPI schema
# and lets Pydantic validate only the selected grant, producing clear,
# branch-specific validation errors instead of ambiguous untagged-union errors.
# A single model would make grant-specific fields optional and leads OpenAPI
# clients such as Postman to generate an unusable `{}` request body.
class EditoPasswordTokenRequest(BaseModel):
    """Password grant requiring both an EDITO username and password."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "grant_type": "password",
                    "username": "YOUR_EDITO_USERNAME",
                    "password": "YOUR_EDITO_PASSWORD",
                }
            ]
        },
    )

    grant_type: Literal["password"]
    username: str = Field(min_length=1, description="EDITO username for password-grant login.")
    password: str = Field(min_length=1, description="EDITO password for password-grant login.")


class EditoRefreshTokenRequest(BaseModel):
    """Refresh grant requiring an EDITO refresh token and no credentials."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"grant_type": "refresh_token", "refresh_token": "YOUR_REFRESH_TOKEN"}]
        },
    )

    grant_type: Literal["refresh_token"]
    refresh_token: str = Field(min_length=1, description="EDITO refresh token for token refresh.")


EditoTokenRequest = Annotated[
    EditoPasswordTokenRequest | EditoRefreshTokenRequest,
    Field(discriminator="grant_type"),
]


class EditoTokenResponse(BaseModel):
    """Token values returned by EDITO Keycloak."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    refresh_expires_in: int | None = None


@dataclass(frozen=True)
class EditoBearerAuth:
    """Validated caller identity extracted from an EDITO bearer token."""

    subject: str
    username: str | None
    claims: dict[str, Any]
    access_token: str

class EditoBearerAuthError(ValueError):
    """Raised when EDITO token exchange or validation fails."""

    def __init__(self, message: str, *, reason: str = "auth_failed") -> None:
        self.reason = reason
        super().__init__(message)


async def exchange_username_password(username: str, password: str) -> EditoTokenResponse:
    """Exchange EDITO username/password credentials for access and refresh tokens."""
    if not username or not password:
        raise EditoBearerAuthError("Username and password are required.")
    return await _exchange_token(
        {
            "grant_type": "password",
            "client_id": _client_id(),
            "username": username,
            "password": password,
            "scope": _scope(),
        },
        grant_type="password",
    )


async def exchange_refresh_token(refresh_token: str) -> EditoTokenResponse:
    """Exchange an EDITO refresh token for a fresh access/refresh token pair."""
    if not refresh_token:
        raise EditoBearerAuthError("Refresh token is required.")
    return await _exchange_token(
        {
            "grant_type": "refresh_token",
            "client_id": _client_id(),
            "refresh_token": refresh_token,
            "scope": _scope(),
        },
        grant_type="refresh_token",
    )


def validate_bearer_token(token: str) -> EditoBearerAuth:
    """Validate an EDITO Keycloak bearer token and return its identity.

    This is the trust boundary for protected EPT routes. It verifies the token
    signature and issuer against Keycloak, then accepts the configured client in
    either the ``aud`` or ``azp`` claim. It returns an ``EditoBearerAuth`` that
    downstream routes can pass to authenticated external-service calls. The
    audience/authorized-party fallback is an explicit compatibility policy and
    should be tightened if the deployed Keycloak realm guarantees one claim.
    """
    if not token:
        raise EditoBearerAuthError("Bearer token is required.", reason="missing_bearer_token")

    issuer = _keycloak_issuer_url()
    expected_client = os.getenv("EDITO_AUTH_AUDIENCE") or _client_id()
    jwks_url = f"{issuer}/protocol/openid-connect/certs"
    logger.info(
        "Validating EDITO bearer token token=%s issuer=%s expected_client=%s",
        token_diagnostic(token),
        issuer,
        expected_client,
    )
    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "RS512"],
            issuer=issuer,
            options={
                "require":
                    [
                        "exp", "iat", "sub"
                    ],
                    "verify_aud": False
                },
        )
    except jwt.PyJWKClientConnectionError as exc:
        # Keycloak/JWKS unreachable is not the same failure as a bad token:
        logger.warning("EDITO bearer token validation unavailable reason=auth_unavailable error=%s", exc)
        raise EditoBearerAuthError(
            "EDITO authentication service is unavailable.",
            reason="auth_unavailable",
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning("EDITO bearer token validation failed reason=invalid_bearer_token error=%s", exc)
        raise EditoBearerAuthError("Invalid bearer token.", reason="invalid_bearer_token") from exc

    if not isinstance(claims, dict) or not _token_matches_client(claims, expected_client):
        logger.warning(
            "EDITO bearer token rejected reason=invalid_bearer_token expected_client=%s",
            expected_client,
        )
        raise EditoBearerAuthError("Invalid bearer token.", reason="invalid_bearer_token")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        logger.warning("EDITO bearer token rejected reason=invalid_bearer_token missing_subject=true")
        raise EditoBearerAuthError("Invalid bearer token.", reason="invalid_bearer_token")
    username = claims.get("preferred_username")
    logger.info(
        "EDITO bearer token accepted subject_present=%s username_present=%s",
        bool(subject),
        isinstance(username, str) and bool(username),
    )
    return EditoBearerAuth(
        subject=subject,
        username=username if isinstance(username, str) else None,
        claims=claims,
        access_token=token,
    )


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """Return a cached PyJWKClient so JWKS fetches are cached across requests."""
    return jwt.PyJWKClient(jwks_url)


def s3_auth() -> None:
    """Placeholder EDITO S3 auth helper; unrelated to API bearer/publishing auth. possible future
    implementation: use EDITO Keycloak client credentials to get a temporary S3 token for EPT to use
    when uploading STAC assets to the EDITO S3 bucket."""
    raise NotImplementedError("EDITO S3 auth is a placeholder and does not read EDITO user credentials.")


async def _exchange_token(data: dict[str, str], *, grant_type: str) -> EditoTokenResponse:
    """Send one token grant to Keycloak and parse the returned token pair."""
    auth_url = _auth_url()
    logger.info("Requesting EDITO token grant_type=%s auth_url=%s client_id=%s", grant_type, auth_url, data["client_id"])
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(auth_url, data=data, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "EDITO token exchange failed grant_type=%s status_code=%s",
                grant_type,
                exc.response.status_code,
            )
            raise EditoBearerAuthError("EDITO authentication failed.", reason="auth_failed") from exc
        except httpx.HTTPError as exc:
            logger.warning("EDITO token exchange unavailable grant_type=%s error=%s", grant_type, exc)
            raise EditoBearerAuthError(
                "EDITO authentication service is unavailable.",
                reason="auth_unavailable",
            ) from exc

        payload = _json_object(response)
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        if not access_token or not refresh_token:
            logger.warning("EDITO token exchange returned incomplete token payload grant_type=%s", grant_type)
            raise EditoBearerAuthError(
                "EDITO authentication response did not include access and refresh tokens.",
                reason="auth_response_missing_tokens",
            )
        token_response = EditoTokenResponse(
            access_token=str(access_token),
            refresh_token=str(refresh_token),
            token_type=str(payload.get("token_type") or "bearer").lower(),
            expires_in=_optional_int(payload.get("expires_in")),
            refresh_expires_in=_optional_int(payload.get("refresh_expires_in")),
        )
        logger.info(
            "EDITO token exchange succeeded grant_type=%s access_token=%s refresh_token=%s expires_in=%s refresh_expires_in=%s",
            grant_type,
            token_diagnostic(token_response.access_token),
            token_diagnostic(token_response.refresh_token),
            token_response.expires_in,
            token_response.refresh_expires_in,
        )
        return token_response


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Parse a Keycloak response body as a JSON object."""
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise EditoBearerAuthError(
            "EDITO authentication response was not valid JSON.",
            reason="auth_response_invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise EditoBearerAuthError(
            "EDITO authentication response was not a JSON object.",
            reason="auth_response_invalid_json",
        )
    return payload


def _auth_url() -> str:
    """Return the configured Keycloak token endpoint URL."""
    return os.getenv("EDITO_AUTH_URL", DEFAULT_EDITO_AUTH_URL).rstrip("/")


def _client_id() -> str:
    """Return the Keycloak client id used by EPT."""
    return os.getenv("EDITO_AUTH_CLIENT_ID", DEFAULT_EDITO_CLIENT_ID)


def _scope() -> str:
    """Return the Keycloak OAuth scope requested by EPT token exchanges."""
    return os.getenv("EDITO_AUTH_SCOPE", DEFAULT_EDITO_SCOPE)


def _keycloak_issuer_url() -> str:
    """Return the issuer URL used for JWT signature and issuer validation."""
    explicit_issuer = os.getenv("EDITO_AUTH_ISSUER_URL", "").strip().strip("\"'").rstrip("/")
    if explicit_issuer:
        return explicit_issuer

    auth_url = _auth_url()
    suffix = "/protocol/openid-connect/token"
    if not auth_url.endswith(suffix):
        raise EditoBearerAuthError(
            "EDITO_AUTH_URL must point to the Keycloak token endpoint.",
            reason="auth_config_invalid",
        )

    return auth_url[: -len(suffix)].rstrip("/")


def _token_matches_client(claims: dict[str, Any], expected_client: str) -> bool:
    """Check whether token claims identify the configured EPT client."""
    audience = claims.get("aud")
    if isinstance(audience, str) and audience == expected_client:
        return True
    if isinstance(audience, list) and expected_client in audience:
        return True
    return claims.get("azp") == expected_client


def _optional_int(value: Any) -> int | None:
    """Convert optional token lifetime values to integers when possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
