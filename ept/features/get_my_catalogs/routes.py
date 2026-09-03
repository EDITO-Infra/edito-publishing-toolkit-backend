"""FastAPI route for catalogs available to the authenticated EDITO user."""

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import AuthFailureDetail, EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.edito_stac_api import (
    EDITO_STAC_ERROR_RESPONSE_EXAMPLE,
    EDITO_STAC_UNAVAILABLE_EXAMPLE,
    EditoStacApiClient,
)

from .models import GetMyCatalogsResponse
from .service import get_my_catalogs


router = APIRouter(prefix="/v1/edito/stac", tags=["STAC Catalogs"])


@router.get(
    "/mycatalogs",
    response_model=GetMyCatalogsResponse,
    response_model_exclude_unset=True,
    summary="Get my catalogs",
    operation_id="getMyCatalogsV1",
    description=(
        "Return the catalog IDs available to the authenticated user from "
        "EDITO STAC. Only IDs beginning with `projects/` are included. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(
        upstream_backed=True,
        upstream_label="EDITO STAC API",
        upstream_unavailable_example=EDITO_STAC_UNAVAILABLE_EXAMPLE,
        upstream_error_example=EDITO_STAC_ERROR_RESPONSE_EXAMPLE,
    ),
)
async def get_my_catalogs_route(
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> GetMyCatalogsResponse:
    """Use the validated bearer identity to retrieve catalogs."""
    if not edito_auth.username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthFailureDetail(
                reason="invalid_bearer_token",
                message="The bearer token is not valid.",
            ).__dict__,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await get_my_catalogs(
        username=edito_auth.username,
        access_token=edito_auth.access_token,
        edito_stac_client=EditoStacApiClient(http_client),
    )
