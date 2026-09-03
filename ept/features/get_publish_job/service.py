"""Use-case orchestration for publishing service job summaries and log pages."""

from __future__ import annotations

import logging
from urllib.parse import quote, urlencode

from ept.infrastructure.services.publishing import (
    PublishingServiceClient,
    PublishingServiceJobDetailResponse,
    PublishingServiceJobRawResponse,
    PublishingServiceJobSummary,
    parse_publishing_next_cursor,
)

from .models import GetPublishJobResponse, JobView, PublishJobSummary

logger = logging.getLogger(__name__)


async def get_publish_job(
    job_id: str,
    view: JobView,
    *,
    limit: int,
    cursor: int | None,
    access_token: str,
    publishing_client: PublishingServiceClient,
) -> GetPublishJobResponse:
    """Return a job summary or one cursor-paginated log page."""
    logger.info(
        "Starting get_publish_job job_id=%s view=%s limit=%s cursor=%s",
        job_id,
        view,
        limit,
        cursor,
    )

    if view == "summary":
        upstream = await publishing_client.get_job(job_id, access_token=access_token, view=view)
        if not isinstance(upstream, PublishingServiceJobSummary):
            raise RuntimeError("Publishing gateway returned an unexpected summary type.")
        response = GetPublishJobResponse.model_validate(
            _map_job_summary(upstream).model_dump(exclude_unset=True)
        )
        logger.info("Completed get_publish_job job_id=%s view=summary", job_id)
        return response

    upstream = await publishing_client.get_job(
        job_id,
        access_token=access_token,
        view=view,
        limit=limit,
        after_id=cursor,
    )
    if not isinstance(upstream, (PublishingServiceJobDetailResponse, PublishingServiceJobRawResponse)):
        raise RuntimeError("Publishing gateway returned an unexpected log type.")
    response = GetPublishJobResponse(
        **_map_job_summary(upstream.job).model_dump(exclude_unset=True),
        logs=[item.model_dump(mode="json") for item in upstream.events.items],
        total=upstream.events.total,
        limit=upstream.events.limit,
        next=_next_page_link(job_id, view, upstream.events.limit, upstream.events.next),
        page_message=upstream.events.message,
    )
    logger.info("Completed get_publish_job job_id=%s view=%s", job_id, view)
    return response


def _map_job_summary(upstream: PublishingServiceJobSummary) -> PublishJobSummary:
    """Map the upstream transport model to the stable public summary."""
    summary = {
        "id": upstream.id,
        "type": upstream.type,
        "status": upstream.status,
        "username": upstream.username,
        "created_at": upstream.created_at,
        "message": upstream.message,
    }
    for field in ("started_at", "finished_at"):
        if field in upstream.model_fields_set:
            summary[field] = getattr(upstream, field)
    return PublishJobSummary.model_validate(summary)


def _next_page_link(
    job_id: str,
    view: JobView,
    limit: int,
    upstream_next: str | None,
) -> str | None:
    """Rewrite an upstream continuation URL as a public EPT URL."""
    if upstream_next is None:
        return None
    cursor = parse_publishing_next_cursor(upstream_next)
    query = urlencode({"view": view, "limit": limit, "cursor": cursor})
    return f"/v1/edito/publish/jobs/{quote(job_id, safe='')}?{query}"
