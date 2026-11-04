"""FastAPI router for the ARCO STAC Item feature."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.edito_auth import EditoBearerAuthError

from .models import ArcoStacItemRequest, ArcoStacItemResponse
from .service import arco_stac_item

router = APIRouter(prefix="/v1/arco", tags=["Make STAC", "arco"])

_REQUEST_EXAMPLES = {
    "linkDefaultOutput": {
        "summary": "Link in, default output: NetCDF asset converted to a Zarr store",
        "description": (
            "Point at any hosted STAC Item (HTTP(S) URL) — no need to stage it "
            "on S3 first. The Item's NetCDF asset is detected and converted "
            "with nc-to-zarr; the updated Item and converted outputs land in "
            "your personal bucket by default."
        ),
        "value": {
            "source_uri": "https://geo.example.org/csw-items/my-dataset.json",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "explicitZip": {
        "summary": "Explicit: pick a conversion for an ambiguous zip asset",
        "description": (
            "Plain .zip assets are never auto-converted — map them explicitly. "
            "The asset key, the href file name, or an href path fragment "
            "(containing '/') works as the mapping key. Converted "
            "assets land under assets_target, the updated Item under item_target."
        ),
        "value": {
            "source_uri": "https://geo.example.org/csw-items/my-dataset.json",
            "bucket": "project-<projectid>",
            "item_target": "my-metadata/items",
            "assets_target": "my-metadata/zip",
            "conversions": {"downloads/data.zip": "gdb-to-parquet"},
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "inPlace": {
        "summary": "In place: replace the source Item JSON (S3 only)",
        "description": (
            "Omit item_target to rewrite the Item at its source location; "
            "converted outputs land next to it (the source folder)."
        ),
        "value": {
            "source_uri": "s3://oidc-<your-username>/my-metadata/my-dataset.json",
            "overwrite": True,
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
}


@router.post(
    "/stac-item",
    response_model=ArcoStacItemResponse,
    summary="ARCO-ify the data assets of a STAC Item",
    operation_id="arcoStacItemV1",
    description=(
        "Reads a STAC Item JSON (local path, s3:// URI, or HTTP(S) URL — "
        "typically the output of POST /v1/metadata/iso-to-stac-item), converts "
        "its data assets to ARCO formats, adds the converted assets to the "
        "Item, and writes the updated Item JSON to "
        "`s3://<bucket>/<item_target>/<item_id>.json`, or in place for S3 "
        "sources when item_target is omitted — a link or local source with no "
        "targets is written to the default location "
        "`s3://<bucket>/<item_id>.json`. Conversions are declared in the "
        "feature's conversions.toml: NetCDF assets (application/x-netcdf, "
        ".nc/.nc4/.cdf, OPeNDAP/DAP URLs) auto-convert with nc-to-zarr; "
        ".gdb/.gdb.zip assets with gdb-to-parquet. Plain .zip assets are "
        "ambiguous and must be mapped explicitly via `conversions` (asset key, "
        "href file name, or href path fragment → declared conversion key, "
        "e.g. nc-to-zarr, "
        "gdb-to-parquet, upload). Converted outputs land under assets_target "
        "(defaults to item_target, else the source folder). The bucket "
        "defaults to your personal EDITO bucket `oidc-<username>` derived "
        "from your bearer token. Long conversions can also run as background "
        "jobs: submit the same payload to POST /v1/runs with operation "
        "'arco.stac-item'. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": _REQUEST_EXAMPLES}}
        }
    },
)
async def arco_stac_item_route(
    request: ArcoStacItemRequest,
    edito_auth: EditoBearerAuthDep,
) -> ArcoStacItemResponse:
    """Authenticate, then run the conversion pipeline off the event loop."""
    try:
        return await arco_stac_item(request, access_token=edito_auth.access_token)
    except EditoBearerAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Failed to fetch source STAC Item: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch source STAC Item: {exc}",
        ) from exc
