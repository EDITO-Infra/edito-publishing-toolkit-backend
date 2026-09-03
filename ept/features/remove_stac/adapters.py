"""Translate between the public remove-STAC contract and upstream DTOs."""

from ept.infrastructure.services.publishing import (
    PublishingServiceQueuedJobResponse,
    PublishingServiceRemoveStacJobRequest,
)

from .models import RemoveStacRequest, RemoveStacResponse


def to_publishing_service_remove_request(
    request: RemoveStacRequest,
) -> PublishingServiceRemoveStacJobRequest:
    """Submits the catalog ID to the publishing service, prepending the catalog root path."""
    return PublishingServiceRemoveStacJobRequest(
        path=f"/catalogs/{request.catalog_id}",
        dry_run=False,
    )


def from_publishing_service_queued_response(
    response: PublishingServiceQueuedJobResponse,
) -> RemoveStacResponse:
    """Return only the queued-job fields promised by the EPT API."""
    return RemoveStacResponse(job_id=response.job_id, status=response.status)
