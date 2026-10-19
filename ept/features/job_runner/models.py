"""Public request/response models for the EPT job-runner feature."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class SubmitRunRequest(BaseModel):
    """One feature operation to execute as a background run."""

    model_config = ConfigDict(extra="forbid")

    operation: str = Field(
        description="Operation key to execute, e.g. 'stac.validate'.",
        min_length=1,
    )
    request: dict[str, Any] = Field(
        default_factory=dict,
        description="Feature request payload, validated against the operation's request model.",
    )


class RunSubmittedResponse(BaseModel):
    """Acknowledgement of a queued EPT run (HTTP 202)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(description="EPT run identifier, prefixed 'run-'.")
    operation: str
    status: RunStatus
    poll_url: str = Field(description="Relative URL to poll for run status and result.")


class RunStatusResponse(BaseModel):
    """Current state of one EPT run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    operation: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # The feature's own response payload (JSON), present once succeeded.
    result: dict[str, Any] | None = None
    # Plain error message, present when failed.
    error: str | None = None


class RunOperationInfo(BaseModel):
    """One runner-capable operation declared in ``operations.toml``."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="Operation key to submit to POST /v1/runs.")
    endpoint: str | None = Field(
        default=None,
        description="OpenAPI route (METHOD /path) accepting the same payload synchronously.",
    )


class RunOperationsResponse(BaseModel):
    """Registry of operations executable as background runs."""

    model_config = ConfigDict(extra="forbid")

    operations: list[RunOperationInfo]


class RunSummary(BaseModel):
    """List view of one EPT run; excludes ``result`` (fetch it via poll)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    operation: str
    status: RunStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunListResponse(BaseModel):
    """Caller-owned runs, newest first."""

    model_config = ConfigDict(extra="forbid")

    runs: list[RunSummary]
