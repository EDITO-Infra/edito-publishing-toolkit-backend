"""FastAPI router for the STAC collection tree builder."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.utils.urlguard import UnsafeUrlError

from .models import CreateCollectionTreeRequest, CreateCollectionTreeResponse
from .service import create_collection_tree


router = APIRouter(prefix="/v1/stac", tags=["Make STAC", "tree", "collection"])


@router.post(
    "/collections/tree",
    response_model=CreateCollectionTreeResponse,
    summary="Create a collection tree from items",
    operation_id="createCollectionTreeV1",
    description=(
        "Create a STAC Collection tree from a set of items — no hand-written "
        "links. The extent is the union of the items' bboxes and datetimes, "
        "licenses are collected from the items via SPDX matching (multiple "
        "distinct licenses are listed under 'licenses'), keywords are unioned, "
        "and a unique flat id is proposed (`<base id>-<slug>` with a base, "
        "else `<username>-<slug>` from your bearer token; an explicit `id` "
        "always wins, no '/' allowed). Items come from `items` (inline JSON), "
        "`item_links` (http(s) URLs, s3:// URIs, or EPT filesystem paths), or "
        "`source_prefix` — a local directory or s3:// prefix whose *.json "
        "files are the items. "
        "Omit `target` to preview: the derived collection JSON is "
        "schema-validated and ready for the STAC Collection builder. Set "
        "`target` to also export a complete, self-contained collection tree "
        "(collection.json plus one file per item) as an S3 prefix in `bucket` "
        "(defaults to your personal EDITO bucket `oidc-<username>`) or, with "
        "`local_output`, into a directory on the EPT filesystem. A "
        "`source_prefix` always exports: collection.json is written inside "
        "the prefix and its item files are re-linked in place. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def create_collection_tree_route(
    request: CreateCollectionTreeRequest,
    edito_auth: EditoBearerAuthDep,
) -> CreateCollectionTreeResponse:
    """Authenticate and run the collection tree pipeline off the event loop."""
    try:
        payload = await run_in_threadpool(
            create_collection_tree,
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
            detail=f"Failed to fetch an item link: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch an item link: {exc}",
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
    return CreateCollectionTreeResponse(**payload)
