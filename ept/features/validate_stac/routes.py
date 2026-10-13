"""FastAPI router for the validate-STAC feature."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import HttpUrl

from ept.api.dependencies import EditoBearerAuthDep, HttpClientDep
from ept.api.errors import problem_responses

from .models import ValidateStacRequest, ValidateStacResponse
from .service import StacFetchError, StacVersionError, validate_stac


router = APIRouter(prefix="/v1/stac", tags=["STAC Validation"])

# Feature-local EDITO STAC API base resolution: strip whitespace and quotes,
# then any trailing slash (semantics identical to the former infrastructure
# resolver, which this feature no longer depends on).
DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"


def resolve_edito_stac_api_url() -> str:
    """Return the configured EDITO STAC API base URL without a trailing slash."""
    configured = os.getenv("EDITO_STAC_API_URL", DEFAULT_EDITO_STAC_API_URL)
    return configured.strip().strip("\"'").rstrip("/")

DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"

# Request example shown in Swagger UI: only the field users need. The
# override fields stay in the schema but are not surfaced as example input.
REQUEST_EXAMPLE = {"stac_url": "https://minio.dive.edito.eu/oidc-myusername/"
    "project-id/catalog-1/catalog.json"}


def _resolve_edito_stac_api_url() -> str:
    """Return the configured EDITO STAC API base URL without a trailing slash."""
    configured = os.getenv("EDITO_STAC_API_URL", DEFAULT_EDITO_STAC_API_URL)
    return configured.strip().strip("\"'").rstrip("/")


def _is_edito_stac_url(url: HttpUrl) -> bool:
    """True when the URL points at the configured EDITO STAC API.

    Only those URLs need the caller's bearer token; public sources
    (e.g. S3-hosted catalogs) reject bearer tokens, so it is never
    attached anywhere else.
    """
    base = _resolve_edito_stac_api_url()
    normalized = str(url).rstrip("/")
    return normalized == base or normalized.startswith(f"{base}/")


@router.post(
    "/validate",
    response_model=ValidateStacResponse,
    summary="Validate a remote STAC object",
    description="Validates a STAC Catalog, Collection, or Item accessible via URL. "
                "Applies EDITO-specific constraints and recurses into children. "
                "Requires `Authorization: Bearer <access_token>`; the token is forwarded "
                "only when the URL targets the EDITO STAC API.",
    responses=problem_responses(upstream_backed=True, upstream_label="Remote STAC source"),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {"example": REQUEST_EXAMPLE}
            }
        }
    },
)
async def post_validate_stac(
    request: ValidateStacRequest,
    edito_auth: EditoBearerAuthDep,
    http_client: HttpClientDep,
) -> ValidateStacResponse:
    """Validate a STAC object.

    The caller's access token is attached only when ``stac_url`` points at
    the configured EDITO STAC API; all other sources are fetched anonymously.
    """
    try:
        return await validate_stac(
            request,
            access_token=edito_auth.access_token if _is_edito_stac_url(request.stac_url) else None,
            http_client=http_client,
        )
    except HTTPException:
        raise
    except StacVersionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except StacFetchError as exc:
        raise HTTPException(
            status_code=exc.status_code or 502, detail=exc.message
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Validation failed: {exc}") from exc
