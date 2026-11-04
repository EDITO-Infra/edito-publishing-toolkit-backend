"""FastAPI routes for publishing service jobs: one job, or the caller's list."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, status

from ept.api.dependencies import AuthFailureDetail, EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses
from ept.features.get_publish_job.transport import (
    PUBLISHING_REJECTED_EXAMPLE,
    PUBLISHING_UNAVAILABLE_EXAMPLE,
)

from .models import (
    DEMO_DETAIL_LOGS,
    DEMO_JOB_ID,
    DEMO_JOB_LIST,
    DEMO_JOB_SUMMARY,
    GetPublishJobQuery,
    GetPublishJobResponse,
    ListPublishJobsQuery,
    PublishJobListResponse,
)
from .service import get_publish_job, list_publish_jobs

router = APIRouter(prefix="/v1/edito/stac/jobs", tags=["Publish STAC", "jobs"])

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
    "/{job_id:uuid}",
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
    http_client: HttpClientDep,
    job_id: Annotated[
        uuid.UUID,
        Path(
            description="Publication job identifier returned by a queued publish or remove request.",
            examples=[DEMO_JOB_ID],
        ),
    ],
    query: Annotated[GetPublishJobQuery, Query()],
) -> GetPublishJobResponse:
    """Validate the public query contract and invoke the use case."""
    return await get_publish_job(
        str(job_id),
        query.view,
        limit=query.limit,
        cursor=query.cursor,
        access_token=edito_bearer_auth.access_token,
        http_client=http_client,
    )


LIST_PUBLISH_JOBS_DESCRIPTION = """
Requires `Authorization: Bearer <access_token>`. Returns every publication and
removal job submitted by the bearer token's EDITO user, ordered from newest to
oldest, capped at `limit`.

The publishing service scopes job reads to the username carried by the bearer
token, so this always lists the caller's own jobs. No refresh token is required.
"""


@router.get(
    "",
    response_model=PublishJobListResponse,
    response_model_exclude_unset=True,
    summary="List my publication jobs",
    operation_id="listPublishJobsV1",
    description=LIST_PUBLISH_JOBS_DESCRIPTION,
    responses={
        200: {
            "description": "Jobs submitted by the requesting EDITO user.",
            "content": {"application/json": {"examples": {"list": {"value": DEMO_JOB_LIST}}}},
        },
        **problem_responses(
            upstream_backed=True,
            upstream_unavailable_example=PUBLISHING_UNAVAILABLE_EXAMPLE,
            upstream_error_example=PUBLISHING_REJECTED_EXAMPLE,
        ),
    },
)
async def list_publish_jobs_route(
    edito_bearer_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
    query: Annotated[ListPublishJobsQuery, Query()],
) -> PublishJobListResponse:
    """List the bearer token's own publication and removal jobs."""
    if not edito_bearer_auth.username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AuthFailureDetail(
                reason="invalid_bearer_token",
                message="The bearer token is not valid.",
            ).__dict__,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await list_publish_jobs(
        limit=query.limit,
        access_token=edito_bearer_auth.access_token,
        http_client=http_client,
    )
