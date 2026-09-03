"""FastAPI route for the remove-STAC feature."""

from fastapi import APIRouter

from ept.api.errors import problem_responses
from ept.infrastructure.services.publishing.dependencies import (
    REFRESH_TOKEN_OPENAPI_PARAMETER,
    PublishingAuthDep,
    PublishingClientDep,
)
from ept.infrastructure.services.publishing.errors import (
    PUBLISHING_REJECTED_EXAMPLE,
    PUBLISHING_UNAVAILABLE_EXAMPLE,
)

from .models import RemoveStacRequest, RemoveStacResponse
from .service import remove_stac

router = APIRouter(prefix="/v1/edito/stac", tags=["STAC Publication and Removal"])


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
    publishing_client: PublishingClientDep,
) -> RemoveStacResponse:
    """Authenticate, delegate the feature flow, and return its public response."""
    return await remove_stac(
        request,
        publishing_auth=publishing_auth,
        publishing_client=publishing_client,
    )
