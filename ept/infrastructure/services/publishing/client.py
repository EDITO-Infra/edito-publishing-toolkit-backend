"""HTTP gateway for the external EDITO publishing service.

Endpoint paths, authentication, upstream query parameters, response validation,
and pagination remain encapsulated here. EPT feature policy belongs outside this
module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
import logging
import os
from typing import Any, Protocol, TypeVar
from urllib.parse import parse_qs, quote, urlparse

import httpx
from pydantic import BaseModel, ValidationError

from ept.infrastructure.utils.logging import sanitize_payload, token_diagnostic

from .errors import PublishingServiceRequestError, PublishingServiceUnavailableError
from .models import (
    PublishingServicePublishStacJobRequest,
    PublishingServiceJobAuth,
    PublishingServiceJobDetailResponse,
    PublishingServiceJobRawResponse,
    PublishingServiceJobResponse,
    PublishingServiceJobSummary,
    PublishingServiceJobView,
    PublishingServiceLogView,
    PublishingServiceQueuedJobResponse,
    PublishingServiceRemoveStacJobRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_PUBLISHING_API_URL = "https://edito-publisher.vliz.be"
DEFAULT_EVENT_PAGE_LIMIT = 1000
MAX_EVENT_PAGES = 10_000

TransportModel = TypeVar("TransportModel", bound=BaseModel)


class AsyncRequestClient(Protocol):
    """Minimal HTTP client contract required by the gateway."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response: ...


def resolve_publishing_api_url() -> str:
    """Return the configured publishing API base URL without a trailing slash."""
    configured = (
        os.getenv("PUBLISHING_API_URL")
        or os.getenv("PUBLISHING_SERVICE_API_URL")
        or DEFAULT_PUBLISHING_API_URL
    )
    return configured.strip().strip("\"'").rstrip("/")



def resolve_publishing_openapi_url() -> str:
    """Return the OpenAPI document URL for the configured publishing service."""
    return f"{resolve_publishing_api_url()}/openapi.json"


class PublishingServiceClient:
    """Send typed, authenticated requests to the EDITO publishing service."""

    def __init__(self, client: AsyncRequestClient, *, publishing_api_url: str | None = None) -> None:
        self._client = client
        self._publishing_api_url = publishing_api_url

    async def publish_stac_job(
        self,
        request: PublishingServicePublishStacJobRequest,
        *,
        auth: PublishingServiceJobAuth,
    ) -> PublishingServiceQueuedJobResponse:
        """Queue a STAC publication job."""
        body = await self._post_job("/stac/publish", auth=auth, payload=request.model_dump())
        return _validate_transport(PublishingServiceQueuedJobResponse, body)

    async def remove_stac_job(
        self,
        request: PublishingServiceRemoveStacJobRequest,
        *,
        auth: PublishingServiceJobAuth,
    ) -> PublishingServiceQueuedJobResponse:
        """Queue a STAC deletion job."""
        body = await self._post_job("/stac/delete", auth=auth, payload=request.model_dump())
        return _validate_transport(PublishingServiceQueuedJobResponse, body)

    async def get_job(
        self,
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

        body = await self._request_json(
            method="GET",
            path=f"/jobs/{quote(job_id, safe='')}",
            access_token=access_token,
            refresh_token=None,
            payload=None,
            query=query,
        )
        if view == "detail":
            return _validate_transport(PublishingServiceJobDetailResponse, body)
        if view == "raw":
            return _validate_transport(PublishingServiceJobRawResponse, body)
        return _validate_transport(PublishingServiceJobSummary, body)

    async def iter_job_events(
        self,
        job_id: str,
        *,
        access_token: str,
        view: PublishingServiceLogView,
        max_pages: int = MAX_EVENT_PAGES,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield all event pages while hiding cursor mechanics from callers.

        Repeated cursors fail closed rather than looping forever. Event IDs already
        seen on an earlier page are skipped to protect exports from page overlap.
        """
        after_id: int | None = None
        seen_cursors: set[int] = set()
        seen_event_ids: set[int] = set()

        for _page_number in range(max_pages):
            response = await self.get_job(
                job_id,
                access_token=access_token,
                view=view,
                limit=DEFAULT_EVENT_PAGE_LIMIT,
                after_id=after_id,
            )
            if not isinstance(response, (PublishingServiceJobDetailResponse, PublishingServiceJobRawResponse)):
                raise PublishingServiceRequestError(502, "Publication backend returned an invalid log page.")

            for event in response.events.items:
                if event.id in seen_event_ids:
                    continue
                seen_event_ids.add(event.id)
                yield event.model_dump(mode="json")

            if response.events.next is None:
                return
            next_cursor = parse_publishing_next_cursor(response.events.next)
            if next_cursor in seen_cursors or next_cursor == after_id:
                raise PublishingServiceRequestError(502, "Publication backend repeated a pagination cursor.")
            seen_cursors.add(next_cursor)
            after_id = next_cursor

        raise PublishingServiceRequestError(502, "Publication backend pagination exceeded the safety limit.")

    async def _post_job(
        self,
        path: str,
        *,
        auth: PublishingServiceJobAuth,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit a job request with the two credentials required upstream."""
        return await self._request_json(
            method="POST",
            path=path,
            access_token=auth.access_token,
            refresh_token=auth.refresh_token,
            payload=payload,
        )

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        access_token: str,
        refresh_token: str | None,
        payload: dict[str, Any] | None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one request and require a JSON object response body."""
        response = await self._request(
            method=method,
            path=path,
            access_token=access_token,
            refresh_token=refresh_token,
            payload=payload,
            query=query,
        )
        return _json_object(response)

    async def _request(
        self,
        *,
        method: str,
        path: str,
        access_token: str,
        refresh_token: str | None,
        payload: dict[str, Any] | None,
        query: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Submit one authenticated request and preserve upstream error context.

        A received non-success response keeps its upstream status and sanitized
        payload. Only transport or response-contract failures receive synthesized
        gateway statuses. Requests are not retried here because job submission is
        not known to be idempotent and an automatic retry could queue duplicates.
        """
        base_url = self._publishing_api_url.rstrip("/") if self._publishing_api_url else resolve_publishing_api_url()
        url = f"{base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        if refresh_token:
            headers["X-EDITO-Refresh-Token"] = refresh_token

        logger.info(
            "Submitting publishing request method=%s url=%s access_token=%s refresh_token=%s payload=%s query=%s",
            method,
            url,
            token_diagnostic(access_token),
            token_diagnostic(refresh_token),
            sanitize_payload(payload or {}),
            sanitize_payload(query or {}),
        )
        try:
            kwargs: dict[str, Any] = {
                "headers": headers,
                "json": payload if method.upper() != "GET" else None,
            }
            if query:
                kwargs["params"] = query
            response = await self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            logger.warning("Publishing service unavailable url=%s error=%s", url, exc)
            raise PublishingServiceUnavailableError() from exc

        body = _response_payload(response)
        logger.info(
            "Publishing service responded method=%s url=%s status_code=%s",
            method,
            url,
            response.status_code,
        )
        if not 200 <= response.status_code < 300:
            logger.warning(
                "Publishing service rejected request url=%s status_code=%s response=%s",
                url,
                response.status_code,
                sanitize_payload(body),
            )
            raise PublishingServiceRequestError(
                response.status_code,
                body,
                retry_after=response.headers.get("Retry-After"),
            )
        return response



def _validate_transport(model: type[TransportModel], body: dict[str, Any]) -> TransportModel:
    """Map an invalid upstream success body to a safe gateway contract failure."""
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        raise PublishingServiceRequestError(502, "Publication backend returned an invalid response.") from exc


def parse_publishing_next_cursor(next_url: str) -> int:
    """Extract the documented cursor from an upstream continuation link."""
    raw_values = parse_qs(urlparse(next_url).query).get("after_id")
    if not raw_values or len(raw_values) != 1:
        raise PublishingServiceRequestError(502, "Publication backend returned an invalid continuation link.")
    try:
        cursor = int(raw_values[0])
    except ValueError as exc:
        raise PublishingServiceRequestError(502, "Publication backend returned an invalid continuation cursor.") from exc
    if cursor < 0:
        raise PublishingServiceRequestError(502, "Publication backend returned an invalid continuation cursor.")
    return cursor



def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Return a JSON object or raise a safe upstream-contract error."""
    body = _response_payload(response)
    if not isinstance(body, dict):
        raise PublishingServiceRequestError(502, "Publication backend returned an invalid response.")
    return body



def _response_payload(response: httpx.Response) -> Any:
    """Parse an upstream response for validation, error reporting, and logging."""
    try:
        return response.json()
    except ValueError:
        return response.text
