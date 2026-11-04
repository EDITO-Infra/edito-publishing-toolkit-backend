"""FastAPI router for EPT job-runner runs.

Runs are EPT-local executions of feature operations, polled and cancelled
here. Publish/remove *jobs* are a different mechanism: they execute on the
external EDITO publishing service and are polled via
``/v1/edito/publish/jobs/{job_id}``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ept.api.dependencies import EditoBearerAuthDep
from ept.api.errors import problem_responses
from ept.infrastructure.services.edito_auth import EditoBearerAuth

from .manager import run_manager
from .models import (
    RunListResponse,
    RunOperationInfo,
    RunOperationsResponse,
    RunStatusResponse,
    RunSubmittedResponse,
    RunSummary,
    SubmitRunRequest,
)
from .operations import registered_operations, resolve_operation, validate_payload

router = APIRouter(prefix="/v1/runs", tags=["EPT Job Runner"])

SUBMIT_EXAMPLE = {
    "operation": "stac.validate",
    "request": {
        "stac_url": "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json",
    },
}


def _owner(auth: EditoBearerAuth) -> str:
    """Stable per-caller identity for run ownership."""
    return auth.username or auth.subject


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "reason": "run_not_found",
            "message": "No run with this identifier exists for the authenticated caller.",
        },
    )


@router.post(
    "",
    response_model=RunSubmittedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a feature operation as an EPT run",
    operation_id="submitFeatureRunV1",
    description=(
        "Queue a long-running EPT feature operation for in-process execution and "
        "return `202` with a `run_id` immediately. Poll `GET /v1/runs/{run_id}` "
        "for status and result; cancel with `DELETE /v1/runs/{run_id}`. "
        "EPT runs are unrelated to publish/remove jobs, which run on the "
        "external EDITO publishing service (`/v1/edito/publish/jobs`). "
        "Requires `Authorization: Bearer <access_token>`."
    ),
    responses=problem_responses(),
    openapi_extra={
        "requestBody": {"content": {"application/json": {"example": SUBMIT_EXAMPLE}}}
    },
)
async def submit_run(
    request: SubmitRunRequest,
    edito_auth: EditoBearerAuthDep,
) -> RunSubmittedResponse:
    """Validate the operation and payload, then schedule the background run."""
    operation = resolve_operation(request.operation)
    if operation is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "reason": "unknown_operation",
                "message": (
                    f"Unknown operation '{request.operation}'. "
                    "See the operation registry for valid keys."
                ),
            },
        )
    try:
        payload = validate_payload(operation, request.request)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "invalid_run_request",
                "message": f"Request payload failed operation validation: {exc}",
            },
        ) from exc
    run = run_manager.submit(
        operation=operation.key,
        runner=operation.run,
        payload=payload,
        access_token=edito_auth.access_token,
        owner=_owner(edito_auth),
    )
    return RunSubmittedResponse(
        run_id=run.run_id,
        operation=run.operation,
        status=run.status,
        poll_url=f"/v1/runs/{run.run_id}",
    )


@router.get(
    "/operations",
    response_model=RunOperationsResponse,
    summary="List registered runner operations",
    operation_id="listFeatureRunOperationsV1",
    description=(
        "Return the operations declared in operations.toml. `endpoint` is the "
        "OpenAPI route (METHOD /path) that accepts the same request payload "
        "synchronously; clients use it to offer background execution."
    ),
    responses=problem_responses(),
)
async def list_operations(edito_auth: EditoBearerAuthDep) -> RunOperationsResponse:
    """Return the operation registry declared in operations.toml."""
    return RunOperationsResponse(
        operations=[
            RunOperationInfo(key=op.key, endpoint=op.endpoint)
            for op in registered_operations().values()
        ]
    )


@router.get(
    "",
    response_model=RunListResponse,
    summary="List caller-owned EPT runs",
    operation_id="listFeatureRunsV1",
    description=(
        "Return all runs owned by the authenticated caller, newest first. "
        "Summaries exclude the result payload; poll GET /v1/runs/{run_id} for it."
    ),
    responses=problem_responses(),
)
async def list_runs(edito_auth: EditoBearerAuthDep) -> RunListResponse:
    """Return caller-owned runs, newest first, without result payloads."""
    runs = run_manager.list_runs(_owner(edito_auth))
    return RunListResponse(
        runs=[
            RunSummary(**{k: v for k, v in run.public_view().items() if k != "result"})
            for run in runs
        ]
    )


@router.get(
    "/{run_id}",
    response_model=RunStatusResponse,
    summary="Poll an EPT run",
    operation_id="getFeatureRunV1",
    description=(
        "Return the status of one EPT run; once `succeeded`, the body "
        "carries the feature's own response payload in `result`. Failed runs "
        "carry a plain `error` message. Only the submitting caller can see a run."
    ),
    responses=problem_responses(),
)
async def get_run(
    run_id: str,
    edito_auth: EditoBearerAuthDep,
) -> RunStatusResponse:
    """Return the current state of one caller-owned run."""
    run = run_manager.get(run_id, owner=_owner(edito_auth))
    if run is None:
        raise _not_found()
    return RunStatusResponse.model_validate(run.public_view())


@router.delete(
    "/{run_id}",
    response_model=RunStatusResponse,
    summary="Cancel an EPT run",
    operation_id="cancelFeatureRunV1",
    description=(
        "Request cancellation of a queued or running EPT run and wait "
        "briefly for it to stop. Cancellation is cooperative: in-flight work "
        "ends at its next await boundary. Terminal runs are returned unchanged."
    ),
    responses=problem_responses(),
)
async def cancel_run(
    run_id: str,
    edito_auth: EditoBearerAuthDep,
) -> RunStatusResponse:
    """Request cancellation of one caller-owned run and report its state."""
    run = run_manager.get(run_id, owner=_owner(edito_auth))
    if run is None:
        raise _not_found()
    await run_manager.cancel(run)
    return RunStatusResponse.model_validate(run.public_view())
