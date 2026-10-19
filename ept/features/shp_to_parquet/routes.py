from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import ShpToParquetRequest, ShpToParquetResponse
from .service import shp_to_parquet


router = APIRouter(prefix="/v1/transform", tags=["Transform Data", "shp", "parquet", "convert"])


@router.post(
    "/shp-to-parquet",
    response_model=ShpToParquetResponse,
    summary="Convert Shapefile to GeoParquet",
    operation_id="shpToParquetV1",
    description=(
        "Convert one or more ESRI shapefiles to GeoParquet — one file per "
        ".shp, with its own schema, CRS (as PROJJSON) and geometry types "
        "(GeoParquet 1.1.0, WKB encoding, ZSTD). Files stream in chunks, so "
        "memory stays flat on large shapefiles. No reprojection. "
        "`source_uri` is a local `.shp` file, a local `.zip` archive "
        "containing one or more, or an HTTP(S) URL of such a `.zip` (a bare "
        "remote .shp URL is rejected: its sidecar files would be missing). "
        "The standard output is S3: "
        "`s3://<bucket>/<target>/<stem>.parquet`, with `bucket` defaulting "
        "to your personal EDITO bucket `oidc-<username>` derived from your "
        "bearer token; `local_output` writes to a directory on the EPT service "
        "filesystem instead. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def shp_to_parquet_route(
    request: ShpToParquetRequest,
    edito_auth: EditoBearerAuthDep,
) -> ShpToParquetResponse:
    """Authenticate and run the synchronous conversion outside the event loop."""
    try:
        return await run_in_threadpool(
            shp_to_parquet,
            request,
            f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except ValueError as exc:
        # Bad source/target/layer selection is a caller-input problem.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
