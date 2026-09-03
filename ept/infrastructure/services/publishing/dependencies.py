"""Shared FastAPI dependencies for publishing-backed EPT routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ept.api.dependencies import (
    AuthFailureDetail,
    EditoBearerAuthDep,
    HttpClientDep,
)
from .client import PublishingServiceClient
from .models import PublishingServiceJobAuth

REFRESH_TOKEN_OPENAPI_PARAMETER = {
    "name": "X-EDITO-Refresh-Token",
    "in": "header",
    "required": True,
    "schema": {"type": "string"},
    "description": (
        "EDITO refresh token returned by `POST /v1/auth`. Required with bearer auth "
        "when submitting publication or removal jobs."
    ),
}


async def get_publishing_service_client(
    client: HttpClientDep,
) -> PublishingServiceClient:
    """Build the single outbound publishing gateway used by a route request."""
    return PublishingServiceClient(client)


async def require_publishing_job_auth(
    edito_bearer_auth: EditoBearerAuthDep,
    refresh_token: str | None = Header(
        default=None,
        alias="X-EDITO-Refresh-Token",
        include_in_schema=False,
    ),
) -> PublishingServiceJobAuth:
    """Require the access and refresh tokens needed to submit an upstream job."""
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthFailureDetail(
                reason="missing_refresh_token",
                message="Provide X-EDITO-Refresh-Token when submitting a publication or removal job.",
            ).__dict__,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return PublishingServiceJobAuth(
        access_token=edito_bearer_auth.access_token,
        refresh_token=refresh_token,
    )


PublishingAuthDep = Annotated[PublishingServiceJobAuth, Depends(require_publishing_job_auth)]
PublishingClientDep = Annotated[PublishingServiceClient, Depends(get_publishing_service_client)]
