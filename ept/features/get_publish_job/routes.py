"""FastAPI route for publishing service summaries and paginated logs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.publishing.dependencies import PublishingClientDep
from ept.infrastructure.services.publishing.errors import (
    PUBLISHING_REJECTED_EXAMPLE,
    PUBLISHING_UNAVAILABLE_EXAMPLE,
)

from .models import (
    DEMO_DETAIL_LOGS,
    DEMO_JOB_ID,
    DEMO_JOB_SUMMARY,
    GetPublishJobQuery,
    GetPublishJobResponse,
)
from .service import get_publish_job

router = APIRouter(prefix="/v1/edito/publish", tags=["Publication Jobs"])

GET_PUBLISH_JOB_DESCRIPTION = """
Requires `Authorization: Bearer <access_token>` on every request. Returns the job
summary by default; select `detail` or `raw` in `view` for one page of logs.

Log pages use cursor pagination because job events can be added while a job is
running. A stable event cursor avoids the shifting-page and database-scan costs
of offset pagination. Follow `next` until it is `null`; clients do not need to
construct cursors themselves. No refresh token is required.
"""
GET_PUBLISH_JOB_SUCCESS_EXAMPLES = {
    "summary": {"summary": "Job summary", "value": DEMO_JOB_SUMMARY},
    "detail": {"summary": "Detailed log page", "value": DEMO_DETAIL_LOGS},
}


@router.get(
    "/jobs/{job_id}",
    response_model=GetPublishJobResponse,
    response_model_exclude_unset=True,
    summary="Get publication job",
    operation_id="getPublicationJobV1",
    description=GET_PUBLISH_JOB_DESCRIPTION,
    responses={
        200: {
            "description": "Job summary or one cursor-paginated log page.",
            "content": {"application/json": {"examples": GET_PUBLISH_JOB_SUCCESS_EXAMPLES}},
        },
        **problem_responses(
            upstream_backed=True,
            upstream_unavailable_example=PUBLISHING_UNAVAILABLE_EXAMPLE,
            upstream_error_example=PUBLISHING_REJECTED_EXAMPLE,
        ),
    },
)
async def get_publish_job_route(
    edito_bearer_auth: EditoBearerAuthDep,
    publishing_client: PublishingClientDep,
    job_id: Annotated[
        str,
        Path(
            description="Publication job identifier returned by a queued publish or remove request.",
            examples=[DEMO_JOB_ID],
        ),
    ],
    query: Annotated[GetPublishJobQuery, Query()],
) -> GetPublishJobResponse:
    """Validate the public query contract and invoke the use case."""
    return await get_publish_job(
        job_id,
        query.view,
        limit=query.limit,
        cursor=query.cursor,
        access_token=edito_bearer_auth.access_token,
        publishing_client=publishing_client,
    )
