"""FastAPI router for the NetCDF-to-STAC-Item feature."""

import logging

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import NetcdfToStacItemRequest, NetcdfToStacItemResponse
from .service import netcdf_to_stac_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/metadata/netcdf", tags=["Metadata Conversion", "netcdf", "convert"])


@router.post(
    "/to-stac-item",
    response_model=NetcdfToStacItemResponse,
    summary="Create a STAC Item from NetCDF metadata",
    description=(
        "Build a STAC Item from a NetCDF source's metadata — dimensions, "
        "coordinates, data variables, and CF attributes, with spatial/temporal "
        "extents derived from coordinates or CF global attributes — and write "
        "it to `s3://<bucket>/<target>/<item_id>.json` (the bucket defaults to "
        "your personal EDITO bucket `oidc-<username>` derived from your bearer "
        "token; `local_output` overrides with a directory on the EPT service "
        "filesystem). Spatial/temporal extents are derived from coordinates or "
        "CF global attributes and can be overridden via `bbox` and "
        "`start_datetime`/`end_datetime` (required in the request when the "
        "dataset has none). The item is standalone unless an explicit "
        "`collection_id` is given — nothing is attached automatically; the id "
        "is used as-is and does not have to exist in the EDITO STAC API yet. "
        "Without one, the response carries a `suggested_collection_id` (a "
        "matching EDITO STAC collection, or a kebab-case id from the dataset's "
        "CF standard_name, https://cfconventions.org) that you can pass as "
        "`collection_id` once you've created or chosen your collection. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def post_netcdf_to_stac_item(
    request: NetcdfToStacItemRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> NetcdfToStacItemResponse:
    """NetCDF work runs in a threadpool; collection resolution stays async."""
    try:
        return await netcdf_to_stac_item(
            request,
            access_token=edito_auth.access_token,
            http_client=http_client,
            default_bucket=f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except ValueError as exc:
        logger.warning("NetCDF to STAC item rejected source=%s detail=%s", request.source_uri, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
