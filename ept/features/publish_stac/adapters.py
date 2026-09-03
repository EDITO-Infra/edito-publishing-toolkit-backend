"""Translate between the public publish-STAC contract and upstream DTOs."""

from ept.infrastructure.services.publishing import (
    PublishingServicePublishStacJobRequest,
    PublishingServiceQueuedJobResponse,
)

from .models import PublishStacRequest, PublishStacResponse


def to_publishing_service_publish_request(
    request: PublishStacRequest,
) -> PublishingServicePublishStacJobRequest:
    """Submit the publish-STAC request to the publishing service.
    using the remote STAC URL, parent path, or dry run or overwrite options."""
    return PublishingServicePublishStacJobRequest(
        # HttpUrl is a Url object; the upstream contract is a plain string.
        remote_stac_url=str(request.remote_stac_url),
        parent_path=f"/catalogs/{request.catalog_id}",
        dry_run=False,
        overwrite=True,
    )


def from_publishing_service_queued_response(
    response: PublishingServiceQueuedJobResponse,
) -> PublishStacResponse:
    """Return only the queued-job fields promised by the EPT API."""
    return PublishStacResponse(job_id=response.job_id, status=response.status)
