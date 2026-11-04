"""FastAPI router for the STAC fetch feature."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import HttpUrl

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import GetStacRequest, GetStacResponse
from .service import fetch_stac_object


router = APIRouter(prefix="/v1/stac", tags=["STAC Tools", "fetch"])

# Feature-local EDITO STAC API base resolution (same semantics as the
# validate_stac feature's local resolver).
DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"


def _is_edito_stac_url(url: HttpUrl) -> bool:
    """True when the URL points at the configured EDITO STAC API.

    Only those URLs need the caller's bearer token; public sources
    (e.g. S3-hosted catalogs) reject bearer tokens, so it is never
    attached anywhere else.
    """
    configured = os.getenv("EDITO_STAC_API_URL", DEFAULT_EDITO_STAC_API_URL)
    base = configured.strip().strip("\"'").rstrip("/")
    normalized = str(url).rstrip("/")
    return normalized == base or normalized.startswith(f"{base}/")


@router.get(
    "/get",
    response_model=GetStacResponse,
    summary="Fetch one STAC object by URL",
    operation_id="getStacV1",
    description=(
        "Fetch a single STAC object (Item, Collection, or Catalog) by URL and "
        "return it as JSON with its detected type. The bearer token is attached "
        "only when the URL targets the EDITO STAC API. Intended for loading "
        "existing objects into the STAC builder for editing. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(upstream_backed=True, upstream_label="Remote STAC source"),
)
async def get_stac_route(
    request: Annotated[GetStacRequest, Depends()],
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> GetStacResponse:
    try:
        stac_object, object_type = await fetch_stac_object(
            str(request.url),
            http_client=http_client,
            access_token=edito_auth.access_token if _is_edito_stac_url(request.url) else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch STAC object: {exc}") from exc
    return GetStacResponse(source=str(request.url), object_type=object_type, stac_object=stac_object)
