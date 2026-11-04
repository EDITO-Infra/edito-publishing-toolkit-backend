"""FastAPI router for the Zarr-to-STAC-Item feature."""

import logging

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import ZarrToStacItemRequest, ZarrToStacItemResponse
from .service import zarr_to_stac_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/metadata/zarr", tags=["Metadata Conversion", "zarr", "convert"])


@router.post(
    "/to-stac-item",
    response_model=ZarrToStacItemResponse,
    summary="Create a STAC Item from Zarr metadata",
    description=(
        "Build a STAC Item from a Zarr store's metadata (v2 or v3; local, S3, "
        "or HTTP) — dimensions, coordinates, data variables, and CF "
        "attributes, with spatial/temporal extents derived from coordinates "
        "or CF global attributes and overridable via `bbox` and "
        "`start_datetime`/`end_datetime` (required in the request when the "
        "dataset has none) — and write it to "
        "`s3://<bucket>/<target>/<item_id>.json` (the bucket defaults to your "
        "personal EDITO bucket `oidc-<username>` derived from your bearer "
        "token; `local_output` overrides with a directory on the EPT service "
        "filesystem). The item is standalone unless an explicit "
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
async def post_zarr_to_stac_item(
    request: ZarrToStacItemRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> ZarrToStacItemResponse:
    """Zarr work runs in a threadpool; collection resolution stays async."""
    try:
        return await zarr_to_stac_item(
            request,
            access_token=edito_auth.access_token,
            http_client=http_client,
            default_bucket=f"oidc-{edito_auth.username}" if edito_auth.username else None,
            default_username=edito_auth.username,
        )
    except ValueError as exc:
        logger.warning("Zarr to STAC item rejected source=%s detail=%s", request.source_uri, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
