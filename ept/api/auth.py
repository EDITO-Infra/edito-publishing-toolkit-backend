"""Public EDITO token routes.

Callers use this endpoint before protected feature routes. The response carries
the access token used in ``Authorization: Bearer``, the refresh token needed
only when submitting STAC publication or removal jobs, and — for password
login — the caller's temporary S3 credentials in ``s3_credentials``.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ept.api.errors import auth_problem_responses
from ept.api.owner_binding import INSTANCE_BOUND, check_owner, subject_from_token
from ept.infrastructure.services.edito_auth import (
    EditoAuthResponse,
    EditoBearerAuthError,
    exchange_auth_credentials,
    exchange_refresh_token,
)

router = APIRouter(prefix="/v1", tags=["Authentication"])
logger = logging.getLogger(__name__)


class EditoPasswordAuthForm(BaseModel):
    """Required public form fields for username/password authentication."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, description="EDITO username.")
    password: str = Field(
        min_length=1,
        description="EDITO password.",
        json_schema_extra={"format": "password"},
    )


class EditoRefreshAuthForm(BaseModel):
    """Required public form field for refresh-token authentication."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(
        min_length=1,
        description="EDITO refresh token.",
        json_schema_extra={"format": "password"},
    )


EditoAuthForm = EditoPasswordAuthForm | EditoRefreshAuthForm


@router.post(
    "/auth",
    response_model=EditoAuthResponse,
    summary="Create EDITO API tokens and S3 credentials",
    operation_id="createEditoTokensV1",
    responses=auth_problem_responses(),
)
async def auth_route(request: Annotated[EditoAuthForm, Form()]) -> EditoAuthResponse:
    """Exchange form-encoded EDITO credentials or a refresh token for API tokens.

    A password grant also returns the caller's temporary 24-hour EDITO S3
    session; a refresh grant rotates only the API tokens because the S3 session
    has its own independent lifetime and Keycloak client. Failure responses
    stay generic so upstream Keycloak details are not exposed through the
    public EPT API.
    """
    grant_type = "refresh_token" if isinstance(request, EditoRefreshAuthForm) else "password"
    logger.info("API auth request started grant_type=%s", grant_type)
    try:
        if isinstance(request, EditoRefreshAuthForm):
            token_response = await exchange_refresh_token(request.refresh_token)
            response = EditoAuthResponse(**token_response.model_dump(), s3_credentials=None)
        else:
            response = await exchange_auth_credentials(request.username, request.password)
        subject, username = subject_from_token(response.access_token)
        if check_owner(subject, username) == INSTANCE_BOUND:
            logger.warning("API auth request rejected reason=%s grant_type=%s", INSTANCE_BOUND, grant_type)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "reason": INSTANCE_BOUND,
                    "message": "This deployment is bound to another EDITO user.",
                },
            )
        return response
    except EditoBearerAuthError as exc:
        logger.warning("API auth request failed grant_type=%s reason=%s", grant_type, exc.reason)
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
