"""FastAPI route for the remove-STAC feature."""

from fastapi import APIRouter

from ept.api.dependencies import HttpClientDep
from ept.api.errors import problem_responses

from .dependencies import REFRESH_TOKEN_OPENAPI_PARAMETER, PublishingAuthDep
from .models import RemoveStacRequest, RemoveStacResponse
from .service import remove_stac

router = APIRouter(prefix="/v1/edito/stac", tags=["Publish STAC", "remove"])

PUBLISHING_UNAVAILABLE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/publication-backend-unavailable",
    "title": "Publication backend unavailable",
    "status": 502,
    "detail": "EPT could not complete the publication request because a required service is unavailable.",
    "reason": "publication_backend_unavailable",
}

PUBLISHING_REJECTED_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/publication-request-rejected",
    "title": "Publication request rejected",
    "status": 400,
    "detail": "The publication request could not be accepted.",
    "reason": "publication_request_rejected",
    "upstream_response": {"detail": "Request rejected by a required service."},
}


@router.post(
    "/remove",
    response_model=RemoveStacResponse,
    status_code=202,
    summary="Queue STAC removal",
    operation_id="queueStacRemovalV1",
    description=(
        "Queue removal of a catalog ID beginning with `projects/`. Requires "
        "`Authorization: Bearer <access_token>` and "
        "`X-EDITO-Refresh-Token: <refresh_token>` headers."
    ),
    responses=problem_responses(
        upstream_backed=True,
        upstream_submission=True,
        upstream_unavailable_example=PUBLISHING_UNAVAILABLE_EXAMPLE,
        upstream_error_example=PUBLISHING_REJECTED_EXAMPLE,
    ),
    openapi_extra={"parameters": [REFRESH_TOKEN_OPENAPI_PARAMETER]},
)
async def remove_stac_route(
    request: RemoveStacRequest,
    publishing_auth: PublishingAuthDep,
    http_client: HttpClientDep,
) -> RemoveStacResponse:
    """Authenticate, delegate the feature flow, and return its public response."""
    return await remove_stac(
        request,
        publishing_auth=publishing_auth,
        http_client=http_client,
    )
