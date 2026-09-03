"""Typed transport models for the external EDITO publishing service.

These models mirror the upstream HTTP contract only. Public EPT models live in
feature packages and are mapped explicitly at that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

PublishingServiceJobView: TypeAlias = Literal["summary", "detail", "raw"]
PublishingServiceLogView: TypeAlias = Literal["detail", "raw"]


class PublishingServicePublishStacJobRequest(BaseModel):
    """JSON body accepted by publishing service ``POST /stac/publish``."""

    model_config = ConfigDict(extra="forbid")

    remote_stac_url: str = Field(min_length=1)
    parent_path: str = Field(min_length=1)
    dry_run: bool = False
    overwrite: bool = True


class PublishingServiceRemoveStacJobRequest(BaseModel):
    """JSON body accepted by publishing service ``POST /stac/delete``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    dry_run: bool = False


class PublishingServiceQueuedJobResponse(BaseModel):
    """Successful response returned after a publishing job is queued."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: Literal["queued"]


class PublishingServiceJobSummary(BaseModel):
    """Lifecycle summary returned by ``GET /jobs/{job_id}``."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    status: str
    username: str
    created_at: datetime | str
    started_at: datetime | str | None = None
    finished_at: datetime | str | None = None
    message: str = Field(min_length=1)


class PublishingServiceDetailEvent(BaseModel):
    """Normalized event row returned by the upstream detail view."""

    model_config = ConfigDict(extra="allow")

    id: int
    job_id: str
    occurred_at: datetime | str
    name: str
    status: str
    level: str
    message: str = Field(min_length=1)
    job_type: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    object_location: str | None = None
    http_method: str | None = None
    http_status: int | None = None
    elapsed_ms: float | None = None


class PublishingServiceRawEvent(BaseModel):
    """Canonical redacted event row returned by the upstream raw view."""

    model_config = ConfigDict(extra="allow")

    id: int
    occurred_at: datetime | str
    name: str
    status: str
    level: str
    message: str = Field(min_length=1)
    job_type: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    object_location: str | None = None
    payload: Any = None


class PublishingServiceDetailEventsPage(BaseModel):
    """One upstream page of normalized job events."""

    model_config = ConfigDict(extra="allow")

    items: list[PublishingServiceDetailEvent]
    total: int
    limit: int
    next: str | None = None
    message: str = Field(min_length=1)


class PublishingServiceRawEventsPage(BaseModel):
    """One upstream page of canonical raw job events."""

    model_config = ConfigDict(extra="allow")

    items: list[PublishingServiceRawEvent]
    total: int
    limit: int
    next: str | None = None
    message: str = Field(min_length=1)


class PublishingServiceJobDetailResponse(BaseModel):
    """Upstream detail representation for one job."""

    model_config = ConfigDict(extra="allow")

    job: PublishingServiceJobSummary
    events: PublishingServiceDetailEventsPage


class PublishingServiceJobRawResponse(BaseModel):
    """Upstream raw representation for one job."""

    model_config = ConfigDict(extra="allow")

    job: PublishingServiceJobSummary
    events: PublishingServiceRawEventsPage


PublishingServiceJobResponse: TypeAlias = (
    PublishingServiceJobSummary
    | PublishingServiceJobDetailResponse
    | PublishingServiceJobRawResponse
)


@dataclass(frozen=True)
class PublishingServiceJobAuth:
    """Access and refresh tokens required for upstream job submission."""

    access_token: str
    refresh_token: str

    def __post_init__(self) -> None:
        if not self.access_token:
            raise ValueError("PublishingServiceJobAuth requires an access token.")
        if not self.refresh_token:
            raise ValueError("PublishingServiceJobAuth requires a refresh token.")
