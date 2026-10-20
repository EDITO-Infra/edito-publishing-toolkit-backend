from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import GdbToParquetRequest, GdbToParquetResponse
from .service import gdb_to_parquet


router = APIRouter(prefix="/v1/transform", tags=["Transform Data", "gdb", "parquet", "convert"])


@router.post(
    "/gdb-to-parquet",
    response_model=GdbToParquetResponse,
    summary="Convert GDB to GeoParquet",
    operation_id="gdbToParquetV1",
    description=(
        "Convert an ESRI file geodatabase to GeoParquet — one file per layer, "
        "own schema and CRS (as PROJJSON) and geometry types "
        "(GeoParquet 1.1.0, WKB encoding, ZSTD). Layers stream in chunks, so "
        "memory stays flat on large geodatabases. No reprojection. "
        "`source_uri` is a local `.gdb` directory, a local `.zip` archive "
        "containing exactly one, or an HTTP(S) URL of such a `.zip` (staged "
        "to a temporary workspace before conversion). The standard output is "
        "S3: `s3://<bucket>/<target>/<layer>.parquet`, with `bucket` defaulting "
        "to your personal EDITO bucket `oidc-<username>` derived from your "
        "bearer token; `local_output` writes to a directory on the EPT service "
        "filesystem instead. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def gdb_to_parquet_route(
    request: GdbToParquetRequest,
    edito_auth: EditoBearerAuthDep,
) -> GdbToParquetResponse:
    """Authenticate and run the synchronous conversion outside the event loop."""
    try:
        return await run_in_threadpool(
            gdb_to_parquet,
            request,
            f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except ValueError as exc:
        # Bad source/target/layer selection is a caller-input problem.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
