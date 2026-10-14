"""EDITO Keycloak token exchange and bearer-token validation.

Everything talks to one Keycloak realm (`EDITO_AUTH_ISSUER_URL`). The flows
differ only in client id and scope:

- API tokens: password or refresh grant for the ``edito`` client.
- S3 session: password grant for the ``onyxia-minio`` client, then that
  identity token is swapped for temporary MinIO STS credentials.
- Bearer verification: JWT check against the same realm's JWKS.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from xml.etree import ElementTree

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field

from ept.infrastructure.utils.logging import token_diagnostic

logger = logging.getLogger(__name__)

# --- configuration --------------------------------------------------------
#
# Only the issuer, the API client id, and the S3 endpoint are configurable;
# everything else is fixed by the EDITO platform.

DEFAULT_ISSUER = "https://auth.dive.edito.eu/auth/realms/datalab"
DEFAULT_S3_ENDPOINT_URL = "https://minio.dive.edito.eu"

API_SCOPE = "openid offline_access"
S3_CLIENT_ID = "onyxia-minio"
S3_SCOPE = "openid email profile"
S3_SESSION_DURATION_SECONDS = 86_400

_TOKEN_PATH = "/protocol/openid-connect/token"
_JWKS_PATH = "/protocol/openid-connect/certs"


def auth_issuer() -> str:
    """Return the Keycloak realm URL all auth endpoints derive from."""
    issuer = os.getenv("EDITO_AUTH_ISSUER_URL", "").strip().strip("\"'").rstrip("/")
    if issuer:
        return issuer
    # Legacy deployments configure the full token endpoint; derive the realm.
    legacy = os.getenv("EDITO_AUTH_URL", "").strip().strip("\"'").rstrip("/")
    if legacy.endswith(_TOKEN_PATH):
        return legacy[: -len(_TOKEN_PATH)]
    return DEFAULT_ISSUER


def _token_url() -> str:
    return auth_issuer() + _TOKEN_PATH


def _client_id() -> str:
    """Return the Keycloak client id EPT API tokens are issued to."""
    return os.getenv("EDITO_AUTH_CLIENT_ID", "edito")


def _s3_endpoint_url() -> str:
    endpoint = os.getenv("EDITO_S3_ENDPOINT_URL", DEFAULT_S3_ENDPOINT_URL)
    endpoint = endpoint.strip().strip("\"'").rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return endpoint


# --- public response models -------------------------------------------------


class EditoTokenResponse(BaseModel):
    """Token values returned by EDITO Keycloak."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int | None = None
    refresh_expires_in: int | None = None


class S3ConnectionParameters(BaseModel):
    """Minimum parameters needed to connect to EDITO S3 storage."""

    model_config = ConfigDict(extra="forbid")

    endpoint_url: str = Field(description="HTTPS endpoint for EDITO S3-compatible storage.")
    access_key_id: str = Field(min_length=1, description="Temporary S3 access key ID.")
    secret_access_key: str = Field(min_length=1, description="Temporary S3 secret access key.")
    # Optional: only needed for STS-issued temporary credentials; static keys
    # (e.g. self-hosted MinIO) connect without one.
    session_token: str | None = Field(
        default=None, min_length=1, description="Temporary S3 session token, if any."
    )

    def storage_options(self) -> dict[str, str]:
        """Return s3fs-compatible options without mutating process environment."""
        options = {
            "key": self.access_key_id,
            "secret": self.secret_access_key,
            "endpoint_url": self.endpoint_url,
        }
        if self.session_token:
            options["token"] = self.session_token
        return options


class S3Credentials(S3ConnectionParameters):
    """Temporary S3 connection parameters plus STS session metadata."""

    expiration: datetime | None = Field(
        default=None,
        description="UTC expiration reported by S3 STS.",
    )


class EditoAuthResponse(EditoTokenResponse):
    """API tokens and, for password login, a temporary EDITO S3 session."""

    s3_credentials: S3Credentials | None = Field(
        description=(
            "Temporary 24-hour EDITO S3 credentials returned by password login. "
            "This is null for refresh-token grants; retain the existing S3 session "
            "until its expiration."
        ),
    )


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


# --- flow 1: API tokens (password and refresh grants) -----------------------


async def exchange_username_password(username: str, password: str) -> EditoTokenResponse:
    """Exchange EDITO username/password credentials for access and refresh tokens."""
    if not username or not password:
        raise EditoBearerAuthError("Username and password are required.")
    payload = await _post_token_grant(
        _client_id(),
        API_SCOPE,
        {"grant_type": "password", "username": username, "password": password},
    )
    return _token_pair(payload)


async def exchange_refresh_token(refresh_token: str) -> EditoTokenResponse:
    """Exchange an EDITO refresh token for a fresh access/refresh token pair."""
    if not refresh_token:
        raise EditoBearerAuthError("Refresh token is required.")
    payload = await _post_token_grant(
        _client_id(),
        API_SCOPE,
        {"grant_type": "refresh_token", "refresh_token": refresh_token},
    )
    return _token_pair(payload)


# --- flow 2: temporary S3 credentials ---------------------------------------


async def exchange_s3_credentials(username: str, password: str) -> S3Credentials:
    """Exchange EDITO credentials for a temporary 24-hour S3 session.

    Two steps: a password grant against the shared Keycloak token endpoint
    yields an identity token the MinIO STS endpoint accepts, then that token
    is swapped for temporary S3 credentials.
    """
    if not username or not password:
        raise EditoBearerAuthError("Username and password are required.")

    payload = await _post_token_grant(
        S3_CLIENT_ID,
        S3_SCOPE,
        {"grant_type": "password", "username": username, "password": password},
    )
    identity_token = payload.get("access_token")
    if not isinstance(identity_token, str) or not identity_token:
        raise EditoBearerAuthError(
            "EDITO authentication response did not include an access token.",
            reason="auth_response_missing_tokens",
        )
    return await _exchange_s3_session(identity_token)


async def exchange_auth_credentials(username: str, password: str) -> EditoAuthResponse:
    """Create API tokens and S3 credentials from one caller login."""
    logger.debug("Starting combined EDITO API and S3 authentication")
    token_response, s3_credentials = await asyncio.gather(
        exchange_username_password(username, password),
        exchange_s3_credentials(username, password),
    )
    logger.debug("Completed combined EDITO API and S3 authentication")
    return EditoAuthResponse(
        **token_response.model_dump(),
        s3_credentials=s3_credentials,
    )


async def _exchange_s3_session(identity_token: str) -> S3Credentials:
    """Swap a Keycloak identity token for temporary MinIO STS credentials."""
    endpoint_url = _s3_endpoint_url()
    # Sent as a form body, not query parameters, so the identity token never
    # appears in request URLs (which httpx logs).
    data = {
        "Action": "AssumeRoleWithWebIdentity",
        "WebIdentityToken": identity_token,
        "DurationSeconds": str(S3_SESSION_DURATION_SECONDS),
        "Version": "2011-06-15",
    }
    logger.debug(
        "Requesting EDITO S3 session endpoint=%s duration_seconds=%d",
        endpoint_url,
        S3_SESSION_DURATION_SECONDS,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(endpoint_url, data=data, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The STS error XML carries a plain error code; safe to log.
            logger.warning(
                "EDITO S3 session exchange failed status_code=%s error=%s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise EditoBearerAuthError("EDITO S3 authentication failed.", reason="auth_failed") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "EDITO S3 session exchange unavailable error_type=%s",
                type(exc).__name__,
            )
            raise EditoBearerAuthError(
                "EDITO S3 authentication service is unavailable.",
                reason="auth_unavailable",
            ) from exc

        values = _parse_s3_credentials(response.text)
        return S3Credentials(endpoint_url=endpoint_url, **values)


# --- flow 3: bearer-token verification --------------------------------------


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

    issuer = auth_issuer()
    expected_client = _client_id()
    jwks_url = f"{issuer}{_JWKS_PATH}"
    logger.debug(
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
    logger.debug(
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


def _token_matches_client(claims: dict[str, Any], expected_client: str) -> bool:
    """Check whether token claims identify the configured EPT client."""
    audience = claims.get("aud")
    if isinstance(audience, str) and audience == expected_client:
        return True
    if isinstance(audience, list) and expected_client in audience:
        return True
    return claims.get("azp") == expected_client


# --- shared Keycloak plumbing -----------------------------------------------


async def _post_token_grant(client_id: str, scope: str, fields: dict[str, str]) -> dict[str, Any]:
    """POST one OAuth grant to the shared token endpoint; return its JSON."""
    data = {"client_id": client_id, "scope": scope, **fields}
    url = _token_url()
    logger.debug(
        "Requesting EDITO token grant_type=%s auth_url=%s client_id=%s",
        fields["grant_type"],
        url,
        client_id,
    )
    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            response = await http.post(url, data=data, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "EDITO token exchange failed grant_type=%s client_id=%s status_code=%s",
                fields["grant_type"],
                client_id,
                exc.response.status_code,
            )
            raise EditoBearerAuthError("EDITO authentication failed.", reason="auth_failed") from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "EDITO token exchange unavailable grant_type=%s client_id=%s error=%s",
                fields["grant_type"],
                client_id,
                exc,
            )
            raise EditoBearerAuthError(
                "EDITO authentication service is unavailable.",
                reason="auth_unavailable",
            ) from exc

        payload = _json_object(response)
        logger.debug(
            "EDITO token exchange succeeded grant_type=%s client_id=%s access_token=%s",
            fields["grant_type"],
            client_id,
            token_diagnostic(str(payload.get("access_token") or "")),
        )
        return payload


def _token_pair(payload: dict[str, Any]) -> EditoTokenResponse:
    """Normalize a Keycloak token payload, requiring the access/refresh pair."""
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    if not access_token or not refresh_token:
        raise EditoBearerAuthError(
            "EDITO authentication response did not include access and refresh tokens.",
            reason="auth_response_missing_tokens",
        )
    return EditoTokenResponse(
        access_token=str(access_token),
        refresh_token=str(refresh_token),
        token_type=str(payload.get("token_type") or "bearer").lower(),
        expires_in=_optional_int(payload.get("expires_in")),
        refresh_expires_in=_optional_int(payload.get("refresh_expires_in")),
    )


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


def _parse_s3_credentials(xml: str) -> dict[str, Any]:
    """Parse an AWS STS-compatible XML credential response."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise EditoBearerAuthError("EDITO S3 returned an invalid response.") from exc

    values = {
        element.tag.rsplit("}", 1)[-1]: (element.text or "").strip()
        for element in root.iter()
    }
    required = {
        "access_key_id": "AccessKeyId",
        "secret_access_key": "SecretAccessKey",
        "session_token": "SessionToken",
    }
    parsed: dict[str, Any] = {field: values.get(tag, "") for field, tag in required.items()}
    if not all(parsed.values()):
        raise EditoBearerAuthError("EDITO S3 did not return complete credentials.")

    expiration = values.get("Expiration")
    if expiration:
        try:
            parsed["expiration"] = datetime.fromisoformat(expiration)
        except ValueError as exc:
            raise EditoBearerAuthError(
                "EDITO S3 returned an invalid credential expiration."
            ) from exc
    else:
        parsed["expiration"] = None
    return parsed


def _optional_int(value: Any) -> int | None:
    """Convert optional token lifetime values to integers when possible."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
