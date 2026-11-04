"""Use-case orchestration for the publish-STAC feature."""

from __future__ import annotations

import httpx

from ept.features.publish_stac.client import publish_stac_job
from ept.features.publish_stac.dependencies import PublishingJobAuth

from .models import PublishStacRequest, PublishStacResponse


async def publish_stac(
    request: PublishStacRequest,
    *,
    publishing_auth: PublishingJobAuth,
    http_client: httpx.AsyncClient,
) -> PublishStacResponse:
    """Build the upstream payload, queue the job, and map the acknowledgement."""
    job = await publish_stac_job(
        http_client,
        {
            # HttpUrl is a Url object; the upstream contract is a plain string.
            "remote_stac_url": str(request.remote_stac_url),
            "parent_path": f"/catalogs/{request.catalog_id}",
            "dry_run": False,
        },
        access_token=publishing_auth.access_token,
        refresh_token=publishing_auth.refresh_token,
    )
    return PublishStacResponse(job_id=job.job_id, status=job.status)
