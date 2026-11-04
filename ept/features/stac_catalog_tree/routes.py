"""FastAPI router for the STAC catalog tree builder."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import CreateCatalogTreeRequest, CreateCatalogTreeResponse
from .service import create_catalog_tree


router = APIRouter(prefix="/v1/stac", tags=["Make STAC", "tree", "catalog"])


@router.post(
    "/catalogs/tree",
    response_model=CreateCatalogTreeResponse,
    summary="Create a catalog tree from collections and catalogs",
    operation_id="createCatalogTreeV1",
    description=(
        "Create one full STAC Catalog tree — no hand-written links. Sources "
        "(combinable): `collections` (inline JSON) and `collection_links` "
        "(http(s) URLs, s3:// URIs, or EPT filesystem paths; each "
        "collection's item files are fetched via its item links) become "
        "direct children, copied under `<target>/<id>/`; `catalogs` (inline) "
        "and `catalog_links` — existing catalogs with collection children — "
        "are combined in too: each source catalog's collection children are "
        "pulled up as direct children of the new catalog (same-id collections "
        "reached through multiple sources are combined once), so merging "
        "existing catalogs and collections is one call. `source_prefix` (a "
        "directory or s3:// prefix of self-contained collection trees, as "
        "exported by the collection tree route) is read the same way. The "
        "catalog's identity/metadata are derived: a unique flat id is "
        "proposed (`<base id>-<slug>` with a base — e.g. your existing EDITO "
        "catalog from GET /v1/edito/stac/mycatalogs — else `<username>-<slug>` "
        "from your bearer token; an explicit `id` always wins), keywords are "
        "unioned and providers merged from the sources; without a base the "
        "first catalog source seeds title/description. Omit the export target "
        "to preview the derived catalog JSON; set `target` (or `local_output`) "
        "to export the complete, publishable, self-contained tree — every "
        "object schema-validated, with a per-object report in the response. "
        "`bucket` defaults to your personal EDITO bucket `oidc-<username>`. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def create_catalog_tree_route(
    request: CreateCatalogTreeRequest,
    edito_auth: EditoBearerAuthDep,
) -> CreateCatalogTreeResponse:
    """Authenticate and run the catalog tree pipeline off the event loop."""
    try:
        payload = await run_in_threadpool(
            create_catalog_tree,
            request,
            default_username=edito_auth.username,
            default_bucket=f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        # Id derivation failures, invalid collections/items, and schema-validation
        # errors are caller-input problems.
        # (UnsafeUrlError is handled above: it subclasses ValueError.)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Failed to fetch a source link: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch a source link: {exc}",
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Missing S3 connection settings surface from the storage layer here.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}") from exc
    return CreateCatalogTreeResponse(**payload)
