from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import ListS3BucketsRequest, ListS3BucketsResponse
from .service import list_s3_buckets


router = APIRouter(prefix="/v1/storage/s3", tags=["Storage & Assets", "buckets", "s3"])


@router.post(
    "/buckets",
    response_model=ListS3BucketsResponse,
    summary="List S3 buckets",
    operation_id="listS3BucketsV1",
    description=(
        "List the S3 buckets the supplied credentials can see on EDITO object "
        "storage. Run this right after POST /v1/auth: pass the returned "
        "`s3_credentials` to discover your personal bucket `oidc-<username>` and "
        "any project buckets `project-<projectid>` your account can access, e.g. "
        "to prefill the `bucket` field of storage requests. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def list_s3_buckets_route(
    request: ListS3BucketsRequest,
    edito_auth: EditoBearerAuthDep,
) -> ListS3BucketsResponse:
    """Authenticate, list buckets outside the event loop, flag the personal bucket."""
    buckets = await run_in_threadpool(list_s3_buckets, request)
    default_bucket = f"oidc-{edito_auth.username}" if edito_auth.username else None
    return ListS3BucketsResponse(
        endpoint_url=request.endpoint_url,
        buckets=buckets,
        default_bucket=default_bucket,
    )
