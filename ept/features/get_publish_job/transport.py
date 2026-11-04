"""Transport models and HTTP access for the publishing service job endpoint.

These models mirror the upstream HTTP contract only. Public EPT models live in
``models.py`` and are mapped explicitly at the service boundary.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Literal, TypeAlias
from urllib.parse import parse_qs, quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ept.infrastructure.services.errors import (
    UpstreamServiceRequestError,
    UpstreamServiceUnavailableError,
)
from ept.infrastructure.utils.logging import sanitize_payload, token_diagnostic

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHING_API_URL = "https://edito-publisher.vliz.be"
DEFAULT_EVENT_PAGE_LIMIT = 1000

PublishingServiceJobView: TypeAlias = Literal["summary", "detail", "raw"]


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


PUBLISHING_UNAVAILABLE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/publication-backend-unavailable",
    "title": "Publication backend unavailable",
    "status": 502,
    "detail": "EPT could not complete the publication request because a required service is unavailable.",
    "reason": "publication_backend_unavailable",
}

PUBLISHING_REJECTED_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/publication-request-rejected",
    "title": "Publication request rejected",
    "status": 400,
    "detail": "The publication request could not be accepted.",
    "reason": "publication_request_rejected",
    "upstream_response": {"detail": "Request rejected by a required service."},
}


def resolve_publishing_api_url() -> str:
    """Return the configured publishing API base URL without a trailing slash."""
    configured = (
        os.getenv("PUBLISHING_API_URL")
        or os.getenv("PUBLISHING_SERVICE_API_URL")
        or DEFAULT_PUBLISHING_API_URL
    )
    return configured.strip().strip("\"'").rstrip("/")


def publishing_unavailable_error() -> UpstreamServiceUnavailableError:
    """Return the stable publication-backend unavailability error."""
    return UpstreamServiceUnavailableError(
        "Failed to reach EDITO publishing service.",
        service_label="Publishing service",
        type_slug="publication-backend-unavailable",
        title="Publication backend unavailable",
        detail="EPT could not complete the publication request because a required service is unavailable.",
        reason="publication_backend_unavailable",
    )


def publishing_rejected_error(
    status_code: int,
    detail: Any,
    *,
    retry_after: str | None = None,
) -> UpstreamServiceRequestError:
    """Return a sanitized rejection that preserves the upstream status."""
    return UpstreamServiceRequestError(
        status_code,
        sanitize_payload(detail),
        service_label="Publishing service",
        type_slug="publication-request-rejected",
        title="Publication request rejected",
        detail="The publication request could not be accepted.",
        reason="publication_request_rejected",
        response_headers={"Retry-After": retry_after} if retry_after else None,
    )


VIEW_RESPONSE_MODELS = {
    "summary": PublishingServiceJobSummary,
    "detail": PublishingServiceJobDetailResponse,
    "raw": PublishingServiceJobRawResponse,
}


async def get_job(
    http_client: httpx.AsyncClient,
    job_id: str,
    *,
    access_token: str,
    view: PublishingServiceJobView = "summary",
    limit: int = DEFAULT_EVENT_PAGE_LIMIT,
    after_id: int | None = None,
) -> PublishingServiceJobResponse:
    """Read one summary or one page of detailed/raw events."""
    query: dict[str, Any] | None = None
    if view != "summary":
        query = {"view": view, "limit": limit}
        if after_id is not None:
            query["after_id"] = after_id

    url = f"{resolve_publishing_api_url()}/jobs/{quote(job_id, safe='')}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    logger.info(
        "Submitting publishing request method=GET url=%s access_token=%s query=%s",
        url,
        token_diagnostic(access_token),
        sanitize_payload(query or {}),
    )
    try:
        response = await http_client.request("GET", url, headers=headers, params=query)
    except httpx.RequestError as exc:
        logger.warning("Publishing service unavailable url=%s error=%s", url, exc)
        raise publishing_unavailable_error() from exc

    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if not 200 <= response.status_code < 300:
        logger.warning(
            "Publishing service rejected request url=%s status_code=%s response=%s",
            url,
            response.status_code,
            sanitize_payload(payload),
        )
        raise publishing_rejected_error(
            response.status_code,
            payload,
            retry_after=response.headers.get("Retry-After"),
        )
    try:
        return VIEW_RESPONSE_MODELS[view].model_validate(payload)
    except ValidationError as exc:
        # A success body without the documented shape has no upstream error status.
        raise publishing_rejected_error(502, "Publication backend returned an invalid response.") from exc


def parse_publishing_next_cursor(next_url: str) -> int:
    """Extract the documented cursor from an upstream continuation link."""
    raw_values = parse_qs(urlparse(next_url).query).get("after_id")
    if not raw_values or len(raw_values) != 1:
        raise publishing_rejected_error(502, "Publication backend returned an invalid continuation link.")
    try:
        cursor = int(raw_values[0])
    except ValueError as exc:
        raise publishing_rejected_error(502, "Publication backend returned an invalid continuation cursor.") from exc
    if cursor < 0:
        raise publishing_rejected_error(502, "Publication backend returned an invalid continuation cursor.")
    return cursor
