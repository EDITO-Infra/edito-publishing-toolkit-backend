"""FastAPI route for the EDITO project catalogs owned by the authenticated user."""

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import AuthFailureDetail, EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.editostacapi import EditoStacApiClient

from .models import GetMyCatalogsResponse
from .service import get_my_catalogs

router = APIRouter(prefix="/v1/edito/stac", tags=["Publish STAC", "catalogs"])

# Representative public responses only; runtime behavior is driven by the
# service exceptions in ept.infrastructure.services.editostacapi.
EDITO_STAC_UNAVAILABLE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-unavailable",
    "title": "EDITO STAC API unavailable",
    "status": 502,
    "detail": "EPT could not retrieve catalogs because the EDITO STAC API is unavailable.",
    "reason": "edito_stac_api_unavailable",
}

EDITO_STAC_ERROR_RESPONSE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-error-response",
    "title": "EDITO STAC API error response",
    "status": 404,
    "detail": "The EDITO STAC API returned an error response.",
    "reason": "edito_stac_api_error_response",
    "upstream_response": {"ErrorCode": 404, "ErrorMessage": "Not Found"},
}


@router.get(
    "/mycatalogs",
    response_model=GetMyCatalogsResponse,
    response_model_exclude_unset=True,
    summary="Get my catalogs",
    operation_id="getMyCatalogsV1",
    description=(
        "Return the IDs of the EDITO STAC project catalogs owned by the "
        "authenticated user (the upstream /users/{username}/catalogs endpoint "
        "lists catalogs the user owns). Only IDs beginning with `projects/` "
        "are included; an empty array means the user has not created any "
        "project catalogs yet. Requires `Authorization: Bearer <access_token>`."
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
        http_client=http_client,
    )
