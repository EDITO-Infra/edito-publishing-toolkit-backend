from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import NetcdfToZarrRequest, NetcdfToZarrResponse
from .service import nc_to_zarr


router = APIRouter(prefix="/v1/transform", tags=["Transform Data", "netcdf", "zarr", "convert"])

_EXAMPLES = {
    "remoteToPersonalBucket": {
        "summary": "Standard: remote NetCDF into your personal bucket",
        "description": (
            "Omitting bucket targets `oidc-<your-username>`, the personal bucket "
            "derived from your bearer token. The store is named after the source "
            "without its .nc extension, so it lands at "
            "s3://oidc-<your-username>/datasets/observations-2025.zarr."
        ),
        "value": {
            "source_uri": "https://data.example.org/datasets/observations-2025.nc",
            "target": "datasets",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "remoteToProjectBucket": {
        "summary": "Remote NetCDF into another bucket you can access (e.g. a project bucket)",
        "description": (
            "Any bucket your account can write to is valid, e.g. project "
            "buckets `project-<projectid>`. Browse your buckets at "
            "https://datalab.dive.edito.eu/file-explorer."
        ),
        "value": {
            "source_uri": "s3://oidc-myproject/raw/observations-2025.nc",
            "bucket": "oidc-myproject",
            "target": "zarr",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "localTargetOverride": {
        "summary": "Override: write the Zarr store to the EPT service filesystem",
        "description": (
            "When `local_output` is used, `bucket` and `target` must be null. It is a "
            "directory on the EPT service filesystem (not the caller's computer); the "
            "store name is appended the same way. Connection parameters can be omitted "
            "entirely for local output."
        ),
        "value": {
            "source_uri": "https://data.example.org/datasets/observations-2025.nc",
            "bucket": None,
            "target": None,
            "local_output": "/workspace/output",
        },
    },
}


@router.post(
    "/nc-to-zarr",
    response_model=NetcdfToZarrResponse,
    summary="Transform NetCDF to Zarr",
    operation_id="transformNetcdfToZarrV1",
    description=(
        "Convert a NetCDF source (local path, S3 URI, or HTTP/OpenDAP/GRIDDAP URL) into "
        "a Zarr store. The standard output is S3: the store — named after the source "
        "without its .nc extension — is written into `target` within `bucket`, which "
        "defaults to your personal EDITO bucket `oidc-<username>` derived from your "
        "bearer token; other buckets your account can access (e.g. project buckets "
        "`project-<projectid>`) are equally valid. Set `local_output` to write to a "
        "directory on the EPT service filesystem instead. Optional overrides: "
        "`output_name`, `engine`, `chunks` (requires Dask), `decode_cf`, "
        "`mask_and_scale`, `consolidated`, `overwrite`, `zarr_format` (defaults to v2 "
        "for S3 output). Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
    openapi_extra={"requestBody": {"content": {"application/json": {"examples": _EXAMPLES}}}},
)
async def nc_to_zarr_route(
    request: NetcdfToZarrRequest,
    edito_auth: EditoBearerAuthDep,
) -> NetcdfToZarrResponse:
    """Authenticate and run the synchronous conversion outside the event loop."""
    default_bucket = f"oidc-{edito_auth.username}" if edito_auth.username else None
    try:
        return await run_in_threadpool(nc_to_zarr, request, default_bucket)
    except ValueError as exc:
        # Bucket-default derivation and connection-parameter errors are caller-input problems.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
