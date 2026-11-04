"""FastAPI router for the STAC collection-suggestion feature."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import SuggestCollectionRequest, SuggestCollectionResponse
from .service import suggest_collection


router = APIRouter(prefix="/v1/stac/suggest", tags=["Make STAC", "suggest", "collection"])


@router.post(
    "/collection",
    response_model=SuggestCollectionResponse,
    summary="Suggest a STAC Collection from items",
    operation_id="suggestStacCollectionV1",
    description=(
        "Derive a STAC Collection from a set of items, step by step: the "
        "extent is the union of the items' bboxes and datetimes, licenses are "
        "collected from the items via SPDX matching (multiple distinct "
        "licenses are listed under 'licenses'), keywords are unioned, and a "
        "unique flat id is proposed (``<base id>-<slug>`` with a base, else "
        "`<username>-<slug>` from your bearer token; an explicit `id` always "
        "wins, no '/' allowed). Items come from `items` (inline), "
        "`item_links` (http(s) URLs, s3:// URIs, or EPT filesystem paths), or "
        "`source_folder` — a local directory or s3:// prefix whose *.json "
        "files are the items. "
        "Omit `target` to preview: the suggested JSON is schema-validated and "
        "ready for the STAC Collection builder (“Edit in STAC Builder”). Set "
        "`target` to also export a complete, self-contained collection tree "
        "(collection.json plus one file per item) as an S3 prefix in `bucket` "
        "(defaults to your personal EDITO bucket `oidc-<username>`) or, with "
        "`local_output`, into a directory on the EPT filesystem. A `source_folder` always "
        "exports: collection.json is written inside the folder and its item "
        "files are re-linked in place — the result is a valid /v1/stac/"
        "assemble source. Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def suggest_collection_route(
    request: SuggestCollectionRequest,
    edito_auth: EditoBearerAuthDep,
) -> SuggestCollectionResponse:
    """Authenticate and run the synchronous suggestion pipeline off the event loop."""
    try:
        payload = await run_in_threadpool(
            suggest_collection,
            request,
            default_username=edito_auth.username,
            default_bucket=f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        # Id derivation failures, invalid items, and schema-validation errors are caller-input problems.
        # (UnsafeUrlError is handled above: it subclasses ValueError.)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"Failed to fetch an item_link: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch an item_link: {exc}",
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
    return SuggestCollectionResponse(**payload)
