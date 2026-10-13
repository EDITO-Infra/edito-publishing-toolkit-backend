"""Authenticated HTTP boundary for the EDITO STAC API.

The client is deliberately generic: callers choose the endpoint path and own
the response contract. Genuine upstream error responses keep their status and
a sanitized payload; EPT synthesizes a gateway status only when no usable
upstream response exists.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ept.infrastructure.services.errors import (
    UpstreamServiceRequestError,
    UpstreamServiceUnavailableError,
)
from ept.infrastructure.utils.logging import sanitize_payload, token_diagnostic

logger = logging.getLogger(__name__)

DEFAULT_EDITO_STAC_API_URL = "https://api.dive.edito.eu/data"


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
    """Wrap an upstream error response with EDITO STAC identity."""

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
    """Send authenticated GET requests to the EDITO STAC API."""

    def __init__(self, client: httpx.AsyncClient, *, edito_stac_api_url: str | None = None) -> None:
        self._client = client
        self._edito_stac_api_url = edito_stac_api_url

    async def get_json(self, path: str, *, access_token: str) -> Any:
        """GET one endpoint path and return the parsed payload."""
        base_url = (
            self._edito_stac_api_url.rstrip("/")
            if self._edito_stac_api_url
            else resolve_edito_stac_api_url()
        )
        url = f"{base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        }
        logger.info(
            "Requesting EDITO STAC API url=%s token=%s", url, token_diagnostic(access_token)
        )
        try:
            response = await self._client.request("GET", url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("EDITO STAC API unavailable url=%s error=%s", url, exc)
            raise EditoStacApiUnavailableError() from exc

        payload = _response_payload(response)
        if not 200 <= response.status_code < 300:
            logger.warning(
                "EDITO STAC API returned an error response url=%s status_code=%s response=%s",
                url,
                response.status_code,
                sanitize_payload(payload),
            )
            raise EditoStacApiRequestError(response.status_code, payload)
        return payload


def _response_payload(response: httpx.Response) -> Any:
    """Parse an upstream response for validation and safe error reporting."""
    try:
        return response.json()
    except ValueError:
        return response.text
