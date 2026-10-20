"""FastAPI router for the STAC Collection builder."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import BuildCollectionRequest, BuildCollectionResponse
from .service import build_collection


router = APIRouter(prefix="/v1/stac/build", tags=["Make STAC", "build", "collection"])

DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"


def _is_edito_stac_url(url) -> bool:
    """Bearer token is attached only when the URL targets the EDITO STAC API."""
    configured = os.getenv("EDITO_STAC_API_URL", DEFAULT_EDITO_STAC_API_URL)
    base = configured.strip().strip("\"'").rstrip("/")
    normalized = str(url).rstrip("/")
    return normalized == base or normalized.startswith(f"{base}/")


REQUEST_EXAMPLES = {
    "s3DefaultBucket": {
        "summary": "Standard: export into your personal bucket (default output)",
        "description": (
            "Omitting bucket exports into `oidc-<your-username>`, the personal "
            "bucket derived from your bearer token. Here the object lands at "
            "s3://oidc-<your-username>/catalog-1/collection.json."
        ),
        "value": {
            "id": "observations-2025",
            "description": "Observations collected in 2025.",
            "license": "CC-BY-4.0",
            "extent": {
                "spatial": {"bbox": [[2.5, 51.0, 4.5, 51.6]]},
                "temporal": {"interval": [["2025-01-01T00:00:00Z", None]]},
            },
            "links": [
                {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
                {"rel": "parent", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
                {"rel": "child", "href": "s3://oidc-<username>/catalog-1/observations-2025-01.json"},
            ],
            "target": "catalog-1",
        },
    },
    "localWorkspaceFolder": {
        "summary": "Export to a local folder instead of S3",
        "description": (
            "local_output is a directory on the EPT filesystem (relative paths "
            "resolve against the EPT working directory). Here the object lands "
            "at ./workspace/catalog-1/collection.json."
        ),
        "value": {
            "id": "observations-2025",
            "description": "Observations collected in 2025.",
            "local_output": "./workspace/catalog-1",
        },
    },
}


@router.post(
    "/collection",
    response_model=BuildCollectionResponse,
    summary="Build a STAC Collection",
    operation_id="buildStacCollectionV1",
    description=(
        "Build a STAC Collection from individual fields, from a complete object "
        "passed as `json`, or from an existing object fetched via `source_url` "
        "(provided fields override the base). Links are the standard STAC `links` "
        "array. Omit `target` to preview; set it to export `collection.json`: "
        "by default it is an S3 prefix inside `bucket` (which defaults to your "
        "personal EDITO bucket `oidc-<username>` derived from your bearer token); "
        "a value ending in `.json` names the object file itself. Set "
        "`local_output` to a directory on the EPT filesystem to write there "
        "instead. Link hrefs are plain and need "
        "not exist yet, so assemble can stitch trees afterwards. Structural spec "
        "violations come back as `warnings`. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="Remote STAC source"),
    openapi_extra={"requestBody": {"content": {"application/json": {"examples": REQUEST_EXAMPLES}}}},
)
async def build_collection_route(
    request: BuildCollectionRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> BuildCollectionResponse:
    try:
        payload = await build_collection(
            request,
            http_client=http_client,
            access_token=(
                edito_auth.access_token
                if request.source_url and _is_edito_stac_url(request.source_url)
                else None
            ),
            default_bucket=f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        # Local export failures (unwritable/missing path) are not upstream fetch problems.
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch base object: {exc}") from exc
    return BuildCollectionResponse(**payload)
