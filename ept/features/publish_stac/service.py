"""Use-case orchestration for the publish-STAC feature."""

from __future__ import annotations

import logging

from ept.infrastructure.services.publishing import PublishingServiceClient, PublishingServiceJobAuth

from .adapters import from_publishing_service_queued_response, to_publishing_service_publish_request
from .models import PublishStacRequest, PublishStacResponse

logger = logging.getLogger(__name__)


async def publish_stac(
    request: PublishStacRequest,
    *,
    publishing_auth: PublishingServiceJobAuth,
    publishing_client: PublishingServiceClient,
) -> PublishStacResponse:
    """Adapt an EPT request, submit it upstream, and adapt the queued response."""
    logger.info(
        "Starting publish_stac remote_stac_url=%s catalog_id=%s",
        request.remote_stac_url,
        request.catalog_id,
    )
    upstream_response = await publishing_client.publish_stac_job(
        to_publishing_service_publish_request(request),
        auth=publishing_auth,
    )
    response = from_publishing_service_queued_response(upstream_response)
    logger.info("Completed publish_stac job_id=%s status=%s", response.job_id, response.status)
    return response
