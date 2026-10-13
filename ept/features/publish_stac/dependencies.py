"""Feature-local authentication dependencies for publishing job submission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from ept.api.dependencies import (
    AuthFailureDetail,
    EditoBearerAuthDep,
)

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


@dataclass(frozen=True)
class PublishingJobAuth:
    """Access and refresh tokens required for upstream job submission."""

    access_token: str
    refresh_token: str


async def require_publishing_job_auth(
    edito_bearer_auth: EditoBearerAuthDep,
    refresh_token: str | None = Header(
        default=None,
        alias="X-EDITO-Refresh-Token",
        include_in_schema=False,
    ),
) -> PublishingJobAuth:
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
    return PublishingJobAuth(
        access_token=edito_bearer_auth.access_token,
        refresh_token=refresh_token,
    )


PublishingAuthDep = Annotated[PublishingJobAuth, Depends(require_publishing_job_auth)]
