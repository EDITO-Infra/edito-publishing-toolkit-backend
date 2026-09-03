"""FastAPI route for the publish-STAC feature."""

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

from .models import PublishStacRequest, PublishStacResponse
from .service import publish_stac

router = APIRouter(prefix="/v1/edito/stac", tags=["STAC Publication and Removal"])


@router.post(
    "/publish",
    response_model=PublishStacResponse,
    status_code=202,
    summary="Queue STAC publication",
    operation_id="queueStacPublicationV1",
    description=(
        "Queue a STAC publication job under a catalog ID beginning with `projects/`. "
        "Requires `Authorization: Bearer <access_token>` and "
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
async def publish_stac_route(
    request: PublishStacRequest,
    publishing_auth: PublishingAuthDep,
    publishing_client: PublishingClientDep,
) -> PublishStacResponse:
    """Authenticate, delegate the feature flow, and return its public response."""
    return await publish_stac(
        request,
        publishing_auth=publishing_auth,
        publishing_client=publishing_client,
    )
