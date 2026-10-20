"""FastAPI router for the STAC assemble feature."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from starlette.concurrency import run_in_threadpool

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses

from .models import AssembleStacRequest, AssembleStacResponse
from .service import assemble_stac


router = APIRouter(prefix="/v1/stac", tags=["Make STAC", "assemble"])


@router.post(
    "/assemble",
    response_model=AssembleStacResponse,
    summary="Assemble a valid STAC from loose JSON files",
    operation_id="assembleStacV1",
    description=(
        "Assemble loose STAC JSON files into one valid, linked STAC tree. "
        "`source_uri` accepts a local directory, a local or remote `.zip` "
        "archive, or an `s3://` URI of a `.zip` object or folder prefix. Every "
        "`*.json` file is classified (catalog, collection, item) and the tree "
        "structure is resolved from the files' own links — `parent`, `child`, "
        "`item` and `collection` rels — with an item's `collection` property "
        "taking precedence and the folder layout as fallback. Fields the items "
        "can provide are derived when missing (collection extents, item bboxes, "
        "ids, `stac_version`), and every exported object is schema-validated "
        "(issues are reported, never fatal). The standard output is S3: the "
        "tree is exported under `s3://<bucket>/<target>`, with `bucket` "
        "defaulting to your personal EDITO bucket `oidc-<username>` derived "
        "from your bearer token; `local_output` writes to a directory on the "
        "EPT service filesystem instead. Requires "
        "`Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
)
async def assemble_stac_route(
    request: AssembleStacRequest,
    edito_auth: EditoBearerAuthDep,
) -> AssembleStacResponse:
    """Authenticate and run the synchronous assembly outside the event loop."""
    try:
        return await run_in_threadpool(
            assemble_stac,
            request,
            f"oidc-{edito_auth.username}" if edito_auth.username else None,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Missing S3 connection settings surface from the storage layer here.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (FileExistsError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if isinstance(exc, FileExistsError) else status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"STAC assemble failed: {exc}") from exc
