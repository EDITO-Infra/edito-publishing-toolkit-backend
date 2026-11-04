from fastapi import APIRouter, Header, HTTPException
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import PROBLEM_BASE_URL, _problem_response_doc, problem_responses
from ept.infrastructure.services.edito_auth import get_s3_session

from .models import ListS3BucketsRequest, ListS3BucketsResponse
from .service import list_s3_buckets

_responses = problem_responses()
_responses[409] = _problem_response_doc(
    "No S3 session available",
    {
        "s3_session_missing": {
            "summary": "No cached S3 session",
            "value": {
                "type": f"{PROBLEM_BASE_URL}/s3-session-missing",
                "title": "S3 session missing",
                "status": 409,
                "detail": (
                    "No S3 credentials available for this caller. Log in again "
                    "via POST /v1/auth (password grant) or supply the "
                    "X-EDITO-S3-* credential headers."
                ),
                "reason": "s3_session_missing",
            },
        }
    },
)

router = APIRouter(prefix="/v1/storage/s3", tags=["Storage & Assets", "buckets", "s3"])


@router.get(
    "/buckets",
    response_model=ListS3BucketsResponse,
    summary="List S3 buckets",
    operation_id="listS3BucketsV1",
    description=(
        "List the S3 buckets the supplied credentials can see on EDITO object "
        "storage, plus your personal bucket `oidc-<username>` and any project "
        "buckets `project-<projectid>` your account can access. After password "
        "login (POST /v1/auth) no credential headers are needed — EPT resolves "
        "the registered S3 session from the bearer token (409 `s3-session-missing` "
        "if none is registered, e.g. after a refresh grant). Alternatively pass "
        "the `s3_credentials` from POST /v1/auth as request headers — "
        "`X-EDITO-S3-Endpoint-Url`, `X-EDITO-S3-Access-Key-Id`, "
        "`X-EDITO-S3-Secret-Access-Key`, and optionally "
        "`X-EDITO-S3-Session-Token` — to override it. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=_responses,
)
async def list_s3_buckets_route(
    edito_auth: EditoBearerAuthDep,
    x_edito_s3_endpoint_url: str | None = Header(
        default=None, alias="X-EDITO-S3-Endpoint-Url"
    ),
    x_edito_s3_access_key_id: str | None = Header(
        default=None, alias="X-EDITO-S3-Access-Key-Id"
    ),
    x_edito_s3_secret_access_key: str | None = Header(
        default=None, alias="X-EDITO-S3-Secret-Access-Key"
    ),
    x_edito_s3_session_token: str | None = Header(
        default=None, alias="X-EDITO-S3-Session-Token"
    ),
) -> ListS3BucketsResponse:
    """Authenticate, resolve credentials from headers or the login cache, list buckets off the event loop."""
    if (
        x_edito_s3_endpoint_url
        and x_edito_s3_access_key_id
        and x_edito_s3_secret_access_key
    ):
        request = ListS3BucketsRequest(
            endpoint_url=x_edito_s3_endpoint_url,
            access_key_id=x_edito_s3_access_key_id,
            secret_access_key=x_edito_s3_secret_access_key,
            session_token=x_edito_s3_session_token,
        )
    else:
        credentials = get_s3_session(edito_auth.subject) if edito_auth.subject else None
        if credentials is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "s3_session_missing",
                    "message": (
                        "No S3 credentials available for this caller. Log in again "
                        "via POST /v1/auth (password grant) or supply the "
                        "X-EDITO-S3-* credential headers."
                    ),
                },
            )
        request = ListS3BucketsRequest(**credentials.model_dump(exclude={"expiration"}))
    buckets = await run_in_threadpool(list_s3_buckets, request)
    default_bucket = f"oidc-{edito_auth.username}" if edito_auth.username else None
    return ListS3BucketsResponse(
        endpoint_url=request.endpoint_url,
        buckets=buckets,
        default_bucket=default_bucket,
    )
