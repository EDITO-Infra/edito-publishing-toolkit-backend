"""Shared exception data for outbound service boundaries.

Service adapters use these exceptions to hand failures to the API layer without
coupling transport code to FastAPI. For a genuine upstream error, adapters should
preserve the upstream status and only the response data that is safe to expose;
EPT then adds a stable service-specific Problem Details envelope. Adapters may
synthesize a gateway status only when there is no usable upstream response, such
as a transport failure or an invalid success payload.
"""

from __future__ import annotations

from typing import Any


class UpstreamServiceUnavailableError(Exception):
    """Describe a failure where no usable HTTP response was received upstream."""

    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        service_label: str,
        type_slug: str,
        title: str,
        detail: str,
        reason: str,
    ) -> None:
        self.service_label = service_label
        self.type_slug = type_slug
        self.title = title
        self.detail = detail
        self.reason = reason
        super().__init__(message)


class UpstreamServiceRequestError(Exception):
    """Carry an upstream error through EPT's public Problem Details boundary.

    ``status_code`` normally remains the status returned by the external service;
    adapters may use a synthesized gateway status for invalid protocol responses.
    ``upstream_response`` is public data once handled by the API layer, so callers
    must sanitize and bound it before constructing this exception. Likewise,
    ``response_headers`` must contain only explicitly approved public headers.
    """

    def __init__(
        self,
        status_code: int,
        upstream_response: Any,
        *,
        service_label: str,
        type_slug: str,
        title: str,
        detail: str,
        reason: str,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.upstream_response = upstream_response
        self.service_label = service_label
        self.type_slug = type_slug
        self.title = title
        self.public_detail = detail
        self.reason = reason
        self.response_headers = response_headers
        super().__init__(f"{service_label} returned HTTP {status_code}.")
