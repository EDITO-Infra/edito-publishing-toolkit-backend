"""Generic FastAPI dependencies shared by API routes."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
import logging
from typing import Annotated

import httpx
from fastapi import HTTPException, Security, status, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ept.infrastructure.services.edito_auth import EditoBearerAuthError, EditoBearerAuth, validate_bearer_token
from ept.infrastructure.utils.logging import token_diagnostic


logger = logging.getLogger(__name__)


bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description=(
        "EDITO access token. Send it as `Authorization: Bearer <access_token>`. "
        "In the API documentation, authorize with the access token only; the "
        "`Bearer` prefix is added automatically."
    ),
)


@dataclass(frozen=True)
class AuthFailureDetail:
    """Safe error body for rejected API auth checks."""

    reason: str
    message: str


async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield the shared outbound HTTP client used by feature routes.

    FastAPI treats this async generator as a per-request dependency. Production
    routes receive a real ``httpx.AsyncClient``; tests override this dependency
    with fake clients so route behavior can be checked without network access.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client


async def require_edito_bearer_auth(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> EditoBearerAuth:
    """Require a valid EDITO Keycloak bearer token for protected API routes.

    This is the base auth dependency for EPT features. It validates the incoming
    ``Authorization: Bearer`` header and returns an ``EditoBearerAuth`` carrying
    the identity claims plus the original access token.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        logger.warning(
            "API auth verification failed reason=missing_bearer_token"
            )
        raise _unauthorized(
            "missing_bearer_token",
            "Provide an Authorization header with a bearer access token.",
        )
    try:
        bearer_auth = await run_in_threadpool(validate_bearer_token, credentials.credentials)
    except EditoBearerAuthError as exc:
        logger.warning(
            "API auth verification failed reason=%s",
            exc.reason
        )
        if exc.reason == "auth_unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=AuthFailureDetail(
                    reason=exc.reason,
                    message="EDITO authentication service is unavailable.",
                ).__dict__,
            ) from exc
        raise _unauthorized(
            exc.reason,
            "The bearer token is not valid."
        ) from exc
    logger.info(
            "API auth verification succeeded token=%s subject_present=%s username_present=%s",
            token_diagnostic(credentials.credentials),
            bool(bearer_auth.subject),
            bool(bearer_auth.username),
        )
    return bearer_auth

def _unauthorized(reason: str, message: str) -> HTTPException:
    """Return a stable 401 response body for auth failures."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AuthFailureDetail(
            reason=reason,
            message=message
        ).__dict__,
        headers={"WWW-Authenticate": "Bearer"},
    )

HttpClientDep = Annotated[
    httpx.AsyncClient,
    Depends(get_http_client),
]

EditoBearerAuthDep = Annotated[
    EditoBearerAuth,
    Depends(require_edito_bearer_auth),
]
