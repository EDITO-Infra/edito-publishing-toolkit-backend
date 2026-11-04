"""FastAPI router for the STAC Item builder."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import BuildItemRequest, BuildItemResponse
from .service import build_item


router = APIRouter(prefix="/v1/stac/objects", tags=["Make STAC", "build", "item"])

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
            "s3://oidc-<your-username>/catalog-1/item.json."
        ),
        "value": {
            "id": "observations-2025-01",
            "bbox": [2.5, 51.0, 4.5, 51.6],
            "datetime": "2025-01-15T10:30:00Z",
            "collection": "observations-2025",
            "assets": {
                "data": {
                    "href": "s3://oidc-<username>/catalog-1/observations-2025-01.zarr",
                    "type": "application/vnd+zarr",
                    "roles": ["data"],
                }
            },
            "links": [
                {"rel": "root", "href": "s3://oidc-<username>/catalog-1/catalog.json"},
                {"rel": "parent", "href": "s3://oidc-<username>/catalog-1/collection.json"},
            ],
            "target": "catalog-1",
        },
    },
    "localWorkspaceFolder": {
        "summary": "Export to a local folder instead of S3",
        "description": (
            "local_output is a directory on the EPT filesystem (relative paths "
            "resolve against the EPT working directory). Here the object lands "
            "at ./workspace/catalog-1/item.json."
        ),
        "value": {
            "id": "observations-2025-01",
            "bbox": [2.5, 51.0, 4.5, 51.6],
            "datetime": "2025-01-15T10:30:00Z",
            "local_output": "./workspace/catalog-1",
        },
    },
}


@router.post(
    "/item",
    response_model=BuildItemResponse,
    summary="Build a STAC Item",
    operation_id="buildStacItemV1",
    description=(
        "Build a STAC Item from individual fields, from a complete object passed "
        "as `json`, or from an existing object fetched via `source_url` (provided "
        "fields override the base). Links are the standard STAC `links` array. "
        "Omit `target` to preview the built JSON; set it to export `item.json`: "
        "by default it is an S3 prefix inside `bucket` (which defaults to your "
        "personal EDITO bucket `oidc-<username>` derived from your bearer token); "
        "a value ending in `.json` names the object file itself. Set `local_output` "
        "to a directory on the EPT filesystem to write there instead. Link hrefs "
        "are plain and need "
        "not exist yet, so the tree routes can stitch trees afterwards. Structural spec "
        "violations come back as `warnings`. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="Remote STAC source"),
    openapi_extra={"requestBody": {"content": {"application/json": {"examples": REQUEST_EXAMPLES}}}},
)
async def build_item_route(
    request: BuildItemRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> BuildItemResponse:
    return await _run_build(request, edito_auth, http_client)


@router.put(
    "/item",
    response_model=BuildItemResponse,
    summary="Replace a STAC Item",
    operation_id="replaceStacItemV1",
    description=(
        "Edit an existing STAC Item: the base comes from `source_url` (fetched, "
        "SSRF-guarded) or inline `stac_json`; provided fields override it with a "
        "shallow merge (provided fields win, the base's other fields survive; "
        "`null` does not delete). Omit `target` to preview the replacement; set "
        "`target`/`local_output` to write it — PUT replaces the object at the "
        "target, and an existing target without `overwrite=true` fails with 409. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="Remote STAC source"),
)
async def replace_item_route(
    request: BuildItemRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> BuildItemResponse:
    if request.stac_json is None and request.source_url is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "PUT replaces an existing Item: provide `stac_json` (inline base) or "
                "`source_url` (fetch the current object). To create one from fields, "
                "use POST /v1/stac/objects/item."
            ),
        )
    return await _run_build(request, edito_auth, http_client)


async def _run_build(
    request: BuildItemRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> BuildItemResponse:
    try:
        payload = await build_item(
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
        # Malformed input (bad geometry/assets dicts, unusable target, no temporal info, …).
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        # Local export failures (unwritable/missing path) are not upstream fetch problems.
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch base object: {exc}") from exc
    return BuildItemResponse(**payload)
