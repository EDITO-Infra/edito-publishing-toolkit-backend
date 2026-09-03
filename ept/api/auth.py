"""Public EDITO token routes.

Callers use this endpoint before protected feature routes. The response carries
the access token used in ``Authorization: Bearer`` and the refresh token needed
only when submitting STAC publication or removal jobs.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from ept.api.errors import auth_problem_responses
from ept.infrastructure.services.edito_auth import (
    EditoBearerAuthError,
    EditoRefreshTokenRequest,
    EditoTokenRequest,
    EditoTokenResponse,
    exchange_refresh_token,
    exchange_username_password,
)

router = APIRouter(prefix="/v1", tags=["Authentication"])
logger = logging.getLogger(__name__)


@router.post(
    "/auth",
    response_model=EditoTokenResponse,
    summary="Create EDITO API tokens",
    operation_id="createEditoTokensV1",
    responses=auth_problem_responses(),
)
async def auth_route(request: EditoTokenRequest) -> EditoTokenResponse:
    """Exchange EDITO credentials or a refresh token for API tokens.

    The route deliberately returns raw tokens to the caller, but failure
    responses stay generic so upstream Keycloak details are not exposed through
    the public EPT API.
    """
    logger.info("API auth request started grant_type=%s", request.grant_type)
    try:
        if isinstance(request, EditoRefreshTokenRequest):
            return await exchange_refresh_token(request.refresh_token)
        return await exchange_username_password(request.username, request.password)
    except EditoBearerAuthError as exc:
        logger.warning("API auth request failed grant_type=%s reason=%s", request.grant_type, exc.reason)
        if exc.reason == "auth_unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason": exc.reason, "message": "EDITO authentication service is unavailable."},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": exc.reason, "message": "EDITO authentication failed."},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
