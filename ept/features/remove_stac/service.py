"""Use-case orchestration for the remove-STAC feature."""

from __future__ import annotations

import logging

from ept.infrastructure.services.publishing import PublishingServiceClient, PublishingServiceJobAuth

from .adapters import from_publishing_service_queued_response, to_publishing_service_remove_request
from .models import RemoveStacRequest, RemoveStacResponse

logger = logging.getLogger(__name__)


async def remove_stac(
    request: RemoveStacRequest,
    *,
    publishing_auth: PublishingServiceJobAuth,
    publishing_client: PublishingServiceClient,
) -> RemoveStacResponse:
    """Adapt an EPT request, submit it upstream, and adapt the queued response."""
    logger.info("Starting remove_stac catalog_id=%s", request.catalog_id)
    upstream_response = await publishing_client.remove_stac_job(
        to_publishing_service_remove_request(request),
        auth=publishing_auth,
    )
    response = from_publishing_service_queued_response(upstream_response)
    logger.info("Completed remove_stac job_id=%s status=%s", response.job_id, response.status)
    return response
