"""Authenticated HTTP transport for the EDITO publishing service.

The feature owns its upstream contract: endpoint path, headers, payload, and
queued-response validation live here. No retry is attempted because job
submission is not idempotent.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ept.infrastructure.services.errors import (
    UpstreamServiceRequestError,
    UpstreamServiceUnavailableError,
)
from ept.infrastructure.utils.logging import sanitize_payload, token_diagnostic

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHING_API_URL = "https://edito-publisher.vliz.be"


class PublishingServiceQueuedJobResponse(BaseModel):
    """Successful response returned after a publishing job is queued."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    status: Literal["queued"]


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


def _payload(response: httpx.Response) -> Any:
    """Parse an upstream response body as JSON, falling back to raw text."""
    try:
        return response.json()
    except ValueError:
        return response.text


async def remove_stac_job(
    http_client: httpx.AsyncClient,
    payload: dict[str, Any],
    *,
    access_token: str,
    refresh_token: str,
) -> PublishingServiceQueuedJobResponse:
    """Queue a STAC deletion job and validate the queued acknowledgement."""
    url = f"{resolve_publishing_api_url()}/stac/delete"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "X-EDITO-Refresh-Token": refresh_token,
    }
    logger.info(
        "Submitting publishing request method=POST url=%s access_token=%s refresh_token=%s payload=%s",
        url,
        token_diagnostic(access_token),
        token_diagnostic(refresh_token),
        sanitize_payload(payload),
    )
    try:
        response = await http_client.request("POST", url, headers=headers, json=payload)
    except httpx.RequestError as exc:
        logger.warning("Publishing service unavailable url=%s error=%s", url, exc)
        raise publishing_unavailable_error() from exc

    if not 200 <= response.status_code < 300:
        logger.warning(
            "Publishing service rejected request url=%s status_code=%s response=%s",
            url,
            response.status_code,
            sanitize_payload(_payload(response)),
        )
        raise publishing_rejected_error(
            response.status_code,
            _payload(response),
            retry_after=response.headers.get("Retry-After"),
        )
    try:
        return PublishingServiceQueuedJobResponse.model_validate(_payload(response))
    except ValidationError as exc:
        # A success body without the queued contract has no upstream error status.
        raise publishing_rejected_error(502, "Publication backend returned an invalid response.") from exc
