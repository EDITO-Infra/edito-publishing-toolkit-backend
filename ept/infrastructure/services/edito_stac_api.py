"""Typed HTTP boundary for the external EDITO STAC API.

The gateway intentionally performs minimal error translation. When EDITO STAC
returns a non-success response, EPT preserves its status and a sanitized version
of its payload while adding a stable EDITO STAC Problem Details identity. EPT
creates its own gateway status only when no upstream response exists or a success
payload violates the response contract.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

from ept.infrastructure.services.errors import (
    UpstreamServiceRequestError,
    UpstreamServiceUnavailableError,
)
from ept.infrastructure.utils.logging import sanitize_payload, token_diagnostic


logger = logging.getLogger(__name__)

DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"

# These dictionaries document representative public responses only. Runtime
# behavior is driven by the service exceptions and the shared API handlers below.
EDITO_STAC_UNAVAILABLE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-unavailable",
    "title": "EDITO STAC API unavailable",
    "status": 502,
    "detail": "EPT could not retrieve catalogs because the EDITO STAC API is unavailable.",
    "reason": "edito_stac_api_unavailable",
}

# The 404 is one example, not a status mapping: any upstream error status is
# preserved and wrapped with the same EDITO STAC identity.
EDITO_STAC_ERROR_RESPONSE_EXAMPLE = {
    "type": "https://api.edito-publishing-toolkit.org/problems/edito-stac-api-error-response",
    "title": "EDITO STAC API error response",
    "status": 404,
    "detail": "The EDITO STAC API returned an error response.",
    "reason": "edito_stac_api_error_response",
    "upstream_response": {"ErrorCode": 404, "ErrorMessage": "Not Found"},
}


class EditoStacCatalogLink(BaseModel):
    """Link contained in an EDITO user-catalog summary."""

    model_config = ConfigDict(extra="allow")

    rel: str
    href: str
    type: str | None = None
    title: str | None = None


class EditoStacCatalogCounters(BaseModel):
    """Counters returned with an EDITO user-catalog summary."""

    model_config = ConfigDict(extra="allow")

    total: int
    # The upstream OpenAPI response schema is incomplete and the live response
    # currently returns this as an empty array. Preserve future element shapes.
    collections: list[Any] = Field(default_factory=list)


class EditoStacUserCatalog(BaseModel):
    """One catalog record returned by ``GET /users/{username}/catalogs``.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    title: str | None = None
    description: str
    links: list[EditoStacCatalogLink]
    level: int
    counters: EditoStacCatalogCounters
    owner: str
    visibility: list[str]
    created: str
    rtype: str | None = None
    stac_url: str | None = None
    pinned: bool
    type: str | None = None


class EditoStacUserCatalogs(RootModel[list[EditoStacUserCatalog]]):
    """Array returned by the EDITO user-catalog endpoint."""


class AsyncRequestClient(Protocol):
    """Minimal async HTTP client contract required by the gateway."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response: ...


class EditoStacApiUnavailableError(UpstreamServiceUnavailableError):
    """Raised when EPT cannot reach the EDITO STAC API."""

    def __init__(self) -> None:
        super().__init__(
            "Failed to reach the EDITO STAC API.",
            service_label="EDITO STAC API",
            type_slug="edito-stac-api-unavailable",
            title="EDITO STAC API unavailable",
            detail=(
                "EPT could not retrieve catalogs because the EDITO STAC API is unavailable."
            ),
            reason="edito_stac_api_unavailable",
        )


class EditoStacApiRequestError(UpstreamServiceRequestError):
    """Add EDITO STAC identity without reinterpreting an upstream error.

    Genuine error responses retain their upstream status and sanitized payload.
    A caller may instead provide a synthesized gateway status when a successful
    response cannot be validated and therefore has no meaningful error status.
    """

    def __init__(self, status_code: int, upstream_response: Any) -> None:
        super().__init__(
            status_code,
            sanitize_payload(upstream_response),
            service_label="EDITO STAC API",
            type_slug="edito-stac-api-error-response",
            title="EDITO STAC API error response",
            detail="The EDITO STAC API returned an error response.",
            reason="edito_stac_api_error_response",
        )


def resolve_edito_stac_api_url() -> str:
    """Return the configured EDITO STAC API base URL without a trailing slash."""
    configured = os.getenv("EDITO_STAC_API_URL", DEFAULT_EDITO_STAC_API_URL)
    return configured.strip().strip("\"'").rstrip("/")


def resolve_edito_stac_openapi_url() -> str:
    """Return the service-description URL for the configured EDITO STAC API."""
    return f"{resolve_edito_stac_api_url()}/api"


class EditoStacApiClient:
    """Send typed, authenticated requests to the EDITO STAC API."""

    def __init__(
        self,
        client: AsyncRequestClient,
        *,
        edito_stac_api_url: str | None = None,
    ) -> None:
        self._client = client
        self._edito_stac_api_url = edito_stac_api_url

    async def get_user_catalogs(
        self,
        username: str,
        *,
        access_token: str,
    ) -> EditoStacUserCatalogs:
        """Return every catalog exposed for the given EDITO username.

        Non-success responses retain their upstream status and sanitized payload.
        Transport failures and invalid success payloads are the only cases where
        this method synthesizes an EPT gateway failure.
        """
        base_url = (
            self._edito_stac_api_url.rstrip("/")
            if self._edito_stac_api_url
            else resolve_edito_stac_api_url()
        )
        url = f"{base_url}/users/{quote(username, safe='')}/catalogs"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        logger.info(
            "Requesting EDITO user catalogs username=%s token=%s",
            username,
            token_diagnostic(access_token),
        )
        try:
            response = await self._client.request("GET", url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("EDITO STAC API unavailable url=%s error=%s", url, exc)
            raise EditoStacApiUnavailableError() from exc

        payload = _response_payload(response)
        # Preserve genuine upstream error statuses instead of maintaining a
        # duplicate status taxonomy that can drift from the external service.
        if not 200 <= response.status_code < 300:
            logger.warning(
                "EDITO STAC API returned an error response url=%s status_code=%s response=%s",
                url,
                response.status_code,
                sanitize_payload(payload),
            )
            raise EditoStacApiRequestError(response.status_code, payload)

        try:
            catalogs = EditoStacUserCatalogs.model_validate(payload)
        except ValidationError as exc:
            # A malformed 2xx response has no upstream error status to preserve.
            # Treat it as a gateway contract failure rather than returning 200.
            logger.warning("EDITO STAC API returned an invalid user-catalog response")
            raise EditoStacApiRequestError(
                502,
                "EDITO STAC API returned an invalid response.",
            ) from exc

        logger.info("Retrieved EDITO user catalogs count=%s", len(catalogs.root))
        return catalogs


def _response_payload(response: httpx.Response) -> Any:
    """Parse an upstream response for validation and safe error reporting."""
    try:
        return response.json()
    except ValueError:
        return response.text
