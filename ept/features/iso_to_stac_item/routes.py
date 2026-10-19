"""FastAPI router for the ISO19139 → STAC Item feature."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import IsoToStacItemRequest, IsoToStacItemResponse
from .service import iso_to_stac_item

router = APIRouter(prefix="/v1/metadata", tags=["Metadata Conversion", "iso19139", "convert"])

_REQUEST_EXAMPLES = {
    "personalBucket": {
        "summary": "Standard: into your personal bucket (bucket omitted)",
        "description": (
            "The Item JSON lands at "
            "s3://oidc-<your-username>/my-metadata/<item_id>.json. "
            "S3 connection parameters come from POST /v1/auth s3_credentials."
        ),
        "value": {
            "source_uri": "https://geo.example.org/csw?service=CSW&request=GetRecordById&id=my-dataset",
            "target": "my-metadata",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "otherBucket": {
        "summary": "Output to another S3 bucket (e.g. a project bucket)",
        "description": (
            "Any bucket your account can access is valid, e.g. project "
            "buckets project-<projectid>; browse them at "
            "https://datalab.dive.edito.eu/file-explorer."
        ),
        "value": {
            "source_uri": "/data/metadata/my-dataset.xml",
            "bucket": "project-<projectid>",
            "target": "my-metadata",
            "endpoint_url": "https://minio.dive.edito.eu",
            "access_key_id": "<access_key_id from POST /v1/auth s3_credentials>",
            "secret_access_key": "<secret_access_key from POST /v1/auth s3_credentials>",
            "session_token": "<session_token from POST /v1/auth s3_credentials>",
        },
    },
    "localOutput": {
        "summary": "Override: local output directory instead of S3",
        "description": (
            "Writes <item_id>.json into the given directory on the EPT "
            "service filesystem. Requires \"target\": null; bucket is "
            "ignored, and no S3 connection parameters are needed."
        ),
        "value": {
            "source_uri": "https://geo.example.org/csw?service=CSW&request=GetRecordById&id=my-dataset",
            "target": None,
            "local_output": "/workspace/my-metadata",
        },
    },
}


@router.post(
    "/iso-to-stac-item",
    response_model=IsoToStacItemResponse,
    summary="Convert an ISO19139 record into a STAC Item",
    operation_id="isoToStacItemV1",
    description=(
        "Reads an ISO19139 metadata record from an http(s) URL, an "
        "`s3://bucket/key` URI, or a path on the EPT service filesystem, maps "
        "it onto a STAC Item (geometry from the geographic bounding box, "
        "temporal extent, keywords, providers, constraints, and online "
        "resources as assets), validates it, and writes the Item JSON to "
        "`s3://<bucket>/<target>/<item_id>.json`. The bucket defaults to your "
        "personal EDITO bucket `oidc-<username>` derived from your bearer "
        "token. Exactly one output is required: either `target` (S3 output, "
        "the standard path) or `local_output` (a directory on the EPT service "
        "filesystem, with `target` set to null). `collection_id` is optional "
        "and only set on the Item if you already know the collection it "
        "belongs to. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": _REQUEST_EXAMPLES}}
        }
    },
)
async def post_iso_to_stac_item(
    request: IsoToStacItemRequest,
    edito_auth: EditoBearerAuthDep,
) -> IsoToStacItemResponse:
    """Authenticate and run the synchronous conversion outside the event loop."""

    default_bucket = f"oidc-{edito_auth.username}" if edito_auth.username else None
    try:
        return await run_in_threadpool(iso_to_stac_item, request, default_bucket)
    except HTTPException:
        raise
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        # Missing bbox/temporal in the ISO record, malformed output options,
        # and missing bucket derivation are caller-input problems.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"source_uri does not contain valid XML: {exc}",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Failed to fetch source ISO19139 record: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch source ISO19139 record: {exc}",
        ) from exc
