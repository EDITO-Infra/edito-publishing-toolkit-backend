"""Reusable HTTP test doubles for the publishing-service gateway."""

from __future__ import annotations

from typing import Any

import httpx

PublishingResponse = tuple[int, dict[str, Any], dict[str, str]]


class RecordingPublishingHttpClient:
    """Record outbound requests and return configured JSON responses.

    Feature tests use ``body`` for a single response or ``responses`` for an
    ordered sequence. Infrastructure tests can pass a response sequence at
    construction time. No network request is made.
    """

    def __init__(
        self,
        responses: list[PublishingResponse] | None = None,
        *,
        status_code: int = 202,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body or {"job_id": "job-1", "status": "queued"}
        self.responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []
        self.last_call: dict[str, Any] = {}

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Record one request and return the next configured response."""
        call: dict[str, Any] = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers,
        }
        if params is not None:
            call["params"] = params
        self.last_call = call
        self.calls.append(call)

        if self.responses:
            status_code, body, response_headers = self.responses.pop(0)
        else:
            status_code, body, response_headers = self.status_code, self.body, {}
        return httpx.Response(
            status_code,
            json=body,
            headers=response_headers,
            request=httpx.Request(method, url),
        )
