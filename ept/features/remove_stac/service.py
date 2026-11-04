"""Use-case orchestration for the remove-STAC feature."""

from __future__ import annotations

import httpx

from ept.features.remove_stac.client import remove_stac_job
from ept.features.remove_stac.dependencies import PublishingJobAuth

from .models import RemoveStacRequest, RemoveStacResponse


async def remove_stac(
    request: RemoveStacRequest,
    *,
    publishing_auth: PublishingJobAuth,
    http_client: httpx.AsyncClient,
) -> RemoveStacResponse:
    """Build the upstream payload, queue the job, and map the acknowledgement."""
    job = await remove_stac_job(
        http_client,
        {
            "path": f"/catalogs/{request.catalog_id}",
            "dry_run": False,
        },
        access_token=publishing_auth.access_token,
        refresh_token=publishing_auth.refresh_token,
    )
    return RemoveStacResponse(job_id=job.job_id, status=job.status)
