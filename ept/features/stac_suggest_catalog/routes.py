"""FastAPI router for the STAC catalog-suggestion feature."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import SuggestCatalogRequest, SuggestCatalogResponse
from .service import suggest_catalog


router = APIRouter(prefix="/v1/stac/suggest", tags=["Make STAC", "suggest", "catalog"])


@router.post(
    "/catalog",
    response_model=SuggestCatalogResponse,
    summary="Suggest a STAC Catalog from collections",
    operation_id="suggestStacCatalogV1",
    description=(
        "Derive a STAC Catalog from a set of collections: a unique flat id "
        "is proposed (`<base id>-<slug>` with a base — e.g. your existing "
        "EDITO catalog from GET /v1/edito/stac/mycatalogs — else "
        "`<username>-<slug>` from your bearer token; an explicit `id` always "
        "wins, no '/' allowed) and title/description are carried over from the "
        "base when present. Catalog metadata is enriched from the collections: "
        "keywords are unioned and providers merged (deduplicated). Sources: "
        "`collections` (inline JSON), `collection_links` (http(s) URLs, "
        "s3:// URIs, or EPT filesystem paths — each collection's item files "
        "are fetched via its item links), or `source_folder` (a directory or "
        "s3:// prefix of self-contained collection trees, as exported by "
        "suggest/collection). Preview mode (no export target with "
        "collection_links, or inline collections/base only) returns the "
        "schema-validated JSON for the STAC Catalog builder. Assemble mode "
        "(`source_folder`, or `collection_links` with `target`) also exports a "
        "complete, publishable tree: `catalog.json` at the target root with "
        "relative child links, each collection copied under `<id>/` (its id, "
        "slugified when unsafe as a folder name) with its root/parent links "
        "re-pointed at the catalog and its item copies re-linked — validate it "
        "with /v1/stac/validate (validate_children). `bucket` defaults to your "
        "personal EDITO bucket `oidc-<username>`; with `local_output` the "
        "tree is written to a directory on the EPT filesystem instead. "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def suggest_catalog_route(
    request: SuggestCatalogRequest,
    edito_auth: EditoBearerAuthDep,
) -> SuggestCatalogResponse:
    """Authenticate and run the suggestion/assembly pipeline off the event loop."""
    try:
        payload = await run_in_threadpool(
            suggest_catalog,
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
            detail=f"Failed to fetch a collection link: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch a collection link: {exc}",
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
    return SuggestCatalogResponse(**payload)
