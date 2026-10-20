from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import UploadS3Request, UploadS3Response
from .service import upload_s3


router = APIRouter(prefix="/v1/storage/s3", tags=["Storage & Assets", "upload", "s3"])

_UPLOAD_EXAMPLES = {
    "remoteDefaultBucket": {
        "summary": "Standard: remote HTTP(S) resource into your personal bucket",
        "description": (
            "Omitting bucket uploads into `oidc-<your-username>`, the personal "
            "bucket derived from your bearer token. Here the object lands at "
            "s3://oidc-<your-username>/my-uploads/observations-2025.nc."
        ),
        "value": {
            "source": "https://data.example.org/datasets/observations-2025.nc",
            "target": "my-uploads",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "remoteProjectBucket": {
        "summary": "Remote resource into another bucket you can access (e.g. a project bucket)",
        "description": (
            "Any bucket your account can write to is valid. Browse your buckets "
            "at https://datalab.dive.edito.eu/file-explorer; project buckets are "
            "typically named `project-<projectid>`."
        ),
        "value": {
            "source": "https://data.example.org/datasets/observations-2025.nc",
            "bucket": "project-<projectid>",
            "target": "my-uploads",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "localSource": {
        "summary": "Upload a file or directory already on the EPT filesystem",
        "description": (
            "A non-URL source is treated as a path on the EPT service "
            "filesystem, not the caller's computer."
        ),
        "value": {
            "source": "/workspace/my-data/observations-2025.nc",
            "target": "my-uploads",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
}


@router.post(
    "/upload",
    response_model=UploadS3Response,
    summary="Upload to S3",
    operation_id="uploadToS3V1",
    description=(
        "Upload an HTTP(S) URL or a file or directory on the EPT service "
        "filesystem to an S3 bucket and destination prefix. The source type is "
        "auto-detected: an http(s) URL is streamed from the remote host, any "
        "other value is treated as a path on the EPT service filesystem. "
        "`bucket` defaults to your personal EDITO bucket `oidc-<username>` derived from "
        "your bearer token; other buckets your account can access (e.g. project buckets "
        "`project-<projectid>`, browsable at https://datalab.dive.edito.eu/file-explorer) "
        "are equally valid. The source name is appended automatically. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
    openapi_extra={"requestBody": {"content": {"application/json": {"examples": _UPLOAD_EXAMPLES}}}},
)
async def upload_s3_route(
    request: UploadS3Request,
    edito_auth: EditoBearerAuthDep,
) -> UploadS3Response:
    """Authenticate and run the synchronous upload outside the event loop."""
    default_bucket = f"oidc-{edito_auth.username}" if edito_auth.username else None
    try:
        return await run_in_threadpool(upload_s3, request, default_bucket)
    except ValueError as exc:
        # Bucket-default derivation and source-name errors are caller-input problems.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
