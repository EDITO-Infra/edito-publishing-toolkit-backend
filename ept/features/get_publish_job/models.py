"""Public models for publishing service summaries, logs, and pagination."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

JobView: TypeAlias = Literal["summary", "detail", "raw"]

DEMO_JOB_ID = "8d7d5a93-ff52-4a8e-9eb6-0d76d874b670"
DEMO_JOB_SUMMARY = {
    "id": DEMO_JOB_ID,
    "type": "stac_publish",
    "status": "succeeded",
    "username": "alice",
    "created_at": "2026-07-01T10:00:00Z",
    "started_at": "2026-07-01T10:00:01Z",
    "finished_at": "2026-07-01T10:00:03Z",
    "message": "Job completed successfully.",
}
DEMO_DETAIL_LOGS = {
    **DEMO_JOB_SUMMARY,
    "logs": [
        {
            "id": 1071,
            "job_id": DEMO_JOB_ID,
            "occurred_at": "2026-07-01T10:00:01Z",
            "name": "stac.object.validate",
            "status": "success",
            "level": "INFO",
            "message": "Validated catalog",
        }
    ],
    "total": 2,
    "limit": 100,
    "page_message": "Returned the first page of job events.",
    "next": (
        f"/v1/edito/publish/jobs/{DEMO_JOB_ID}"
        "?view=detail&limit=100&cursor=1071"
    ),
}


class GetPublishJobQuery(BaseModel):
    """User-facing options for a publishing service lookup."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    view: JobView = Field(
        default="summary",
        description="Response view: job summary, readable logs, or canonical raw logs.",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum log records in a detail or raw page.",
    )
    cursor: int | None = Field(
        default=None,
        ge=0,
        description="Continuation cursor from the previous page's next link.",
    )

    @model_validator(mode="after")
    def validate_cursor_view(self) -> Self:
        """Reject a continuation cursor when no log page was requested."""
        if self.view == "summary" and self.cursor is not None:
            raise ValueError("cursor requires view=detail or view=raw")
        return self


class PublishJobSummary(BaseModel):
    """Stable EPT lifecycle summary of a submitted publication or removal job."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    id: str = Field(description="Publication job identifier.")
    type: str = Field(description="Publication job type.")
    status: str = Field(description="Current job status.")
    username: str = Field(description="EDITO username that submitted the job.")
    created_at: datetime | str = Field(description="Time the job was created.")
    started_at: datetime | str | None = Field(
        default=None,
        description="Time job execution started.",
    )
    finished_at: datetime | str | None = Field(
        default=None,
        description="Time job execution finished.",
    )
    message: str = Field(min_length=1, description="Publisher-provided description of the current job state.")


class GetPublishJobResponse(PublishJobSummary):
    """Job summary, optionally extended with one detailed or raw log page."""

    logs: list[dict[str, Any]] | None = Field(
        default=None,
        description="Detailed or raw log records in ascending event order.",
    )
    total: int | None = Field(default=None, description="Total number of matching log records.")
    limit: int | None = Field(default=None, description="Maximum records requested for this page.")
    next: str | None = Field(
        default=None,
        description="EPT URL for the next page, or null when this is the final page.",
    )
    page_message: str | None = Field(
        default=None,
        description="Publisher-provided description of the returned log page.",
    )

    @model_validator(mode="after")
    def validate_log_page_fields(self) -> Self:
        """Require all log-page fields together so partial public shapes cannot escape."""
        page_fields = {"logs", "total", "limit", "next", "page_message"}
        present_fields = page_fields.intersection(self.model_fields_set)
        if present_fields and present_fields != page_fields:
            raise ValueError("logs, total, limit, next, and page_message must be provided together")
        return self
