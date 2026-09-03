"""Safe errors and public examples for the publishing-service boundary."""

from __future__ import annotations

from typing import Any

from ept.infrastructure.services.errors import UpstreamServiceRequestError, UpstreamServiceUnavailableError
from ept.infrastructure.utils.logging import sanitize_payload

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


class PublishingServiceUnavailableError(UpstreamServiceUnavailableError):
    """Raised when EPT cannot reach the external publishing service."""

    reason = "publication_backend_unavailable"

    def __init__(self, message: str = "Failed to reach EDITO publishing service.") -> None:
        super().__init__(
            message,
            service_label="Publishing service",
            type_slug="publication-backend-unavailable",
            title="Publication backend unavailable",
            detail="EPT could not complete the publication request because a required service is unavailable.",
            reason=self.reason,
        )


class PublishingServiceRequestError(UpstreamServiceRequestError):
    """Raised when the publishing service rejects a request or response shape."""

    def __init__(self, status_code: int, detail: Any, *, retry_after: str | None = None) -> None:
        sanitized_detail = sanitize_payload(detail)
        self.reason = "publication_request_rejected"
        super().__init__(
            status_code,
            sanitized_detail,
            service_label="Publishing service",
            type_slug="publication-request-rejected",
            title="Publication request rejected",
            detail="The publication request could not be accepted.",
            reason=self.reason,
            response_headers={"Retry-After": retry_after} if retry_after else None,
        )
        self.detail = sanitized_detail
