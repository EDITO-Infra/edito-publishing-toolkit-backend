"""Tests for the typed EDITO STAC API gateway."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from ept.infrastructure.services.edito_stac_api import (
    DEFAULT_EDITO_STAC_API_URL,
    EditoStacApiClient,
    EditoStacApiRequestError,
    EditoStacApiUnavailableError,
    EditoStacUserCatalogs,
    resolve_edito_stac_api_url,
    resolve_edito_stac_openapi_url,
)
from tests.conftest import integration, unit


CATALOG_PAYLOAD = [
    {
        "id": "projects/demo",
        "title": None,
        "description": "Demo project catalog",
        "links": [
            {
                "rel": "self",
                "href": "https://stac.test/catalogs/projects%2Fdemo",
                "type": "application/json",
                "future_link_field": "preserved",
            }
        ],
        "level": 2,
        "counters": {"total": 4, "collections": [], "future_counter": 3},
        "owner": "alice",
        "visibility": ["public"],
        "created": "2026-08-01T10:00:00Z",
        "rtype": "catalog",
        "stac_url": None,
        "pinned": False,
        "future_catalog_field": {"preserved": True},
    }
]


class RecordingHttpClient:
    """Small outbound fake that records requests and returns one response."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: Any = CATALOG_PAYLOAD,
        error: httpx.RequestError | None = None,
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        """Record one request and return the configured result."""
        self.calls.append({"method": method, "url": url, "headers": headers})
        request = httpx.Request(method, url)
        if self.error is not None:
            raise self.error
        if isinstance(self.payload, RawText):
            return httpx.Response(
                self.status_code,
                text=self.payload.value,
                request=request,
            )
        return httpx.Response(
            self.status_code,
            json=self.payload,
            request=request,
        )


class RawText:
    """Signal that a fake response body is not JSON."""

    def __init__(self, value: str) -> None:
        self.value = value


@unit
def test_edito_stac_url_resolution(monkeypatch: pytest.MonkeyPatch):
    """Runtime and OpenAPI URLs share one normalized configuration source."""
    monkeypatch.delenv("EDITO_STAC_API_URL", raising=False)
    assert resolve_edito_stac_api_url() == DEFAULT_EDITO_STAC_API_URL
    assert resolve_edito_stac_openapi_url() == f"{DEFAULT_EDITO_STAC_API_URL}/api"

    monkeypatch.setenv("EDITO_STAC_API_URL", '"https://stac.test/data/"')
    assert resolve_edito_stac_api_url() == "https://stac.test/data"
    assert resolve_edito_stac_openapi_url() == "https://stac.test/data/api"


@unit
def test_get_user_catalogs_sends_auth_and_preserves_response():
    """The gateway encodes usernames and retains the complete upstream payload."""
    fake = RecordingHttpClient()
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data/")

    response = asyncio.run(
        client.get_user_catalogs("alice/example", access_token="access-secret")
    )

    assert isinstance(response, EditoStacUserCatalogs)
    assert response.model_dump(mode="json", exclude_unset=True) == CATALOG_PAYLOAD
    assert fake.calls == [
        {
            "method": "GET",
            "url": "https://stac.test/data/users/alice%2Fexample/catalogs",
            "headers": {
                "Accept": "application/json",
                "Authorization": "Bearer access-secret",
            },
        }
    ]


@unit
def test_get_user_catalogs_accepts_an_empty_array():
    """A user with no available catalogs receives a valid empty response."""
    fake = RecordingHttpClient(payload=[])
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data")

    response = asyncio.run(client.get_user_catalogs("alice", access_token="token"))

    assert response.root == []


@unit
def test_get_user_catalogs_wraps_upstream_error_and_redacts_tokens():
    """An upstream error keeps its status and sanitized response payload."""
    fake = RecordingHttpClient(
        status_code=404,
        payload={"ErrorMessage": "Not Found", "access_token": "leaked"},
    )
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data")

    with pytest.raises(EditoStacApiRequestError) as raised:
        asyncio.run(client.get_user_catalogs("alice", access_token="token"))

    assert raised.value.status_code == 404
    assert raised.value.type_slug == "edito-stac-api-error-response"
    assert raised.value.title == "EDITO STAC API error response"
    assert raised.value.public_detail == (
        "The EDITO STAC API returned an error response."
    )
    assert raised.value.reason == "edito_stac_api_error_response"
    assert raised.value.upstream_response == {
        "ErrorMessage": "Not Found",
        "access_token": "***REDACTED***",
    }


@unit
def test_get_user_catalogs_preserves_other_upstream_error_statuses():
    """The generic EDITO STAC envelope does not reinterpret upstream statuses."""
    fake = RecordingHttpClient(
        status_code=503,
        payload={"ErrorCode": 503, "ErrorMessage": "Service Unavailable"},
    )
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data")

    with pytest.raises(EditoStacApiRequestError) as raised:
        asyncio.run(client.get_user_catalogs("alice", access_token="token"))

    assert raised.value.status_code == 503
    assert raised.value.type_slug == "edito-stac-api-error-response"
    assert raised.value.title == "EDITO STAC API error response"
    assert raised.value.public_detail == (
        "The EDITO STAC API returned an error response."
    )
    assert raised.value.reason == "edito_stac_api_error_response"


@unit
@pytest.mark.parametrize(
    "payload",
    [{"catalogs": []}, [{}], RawText("not-json")],
)
def test_get_user_catalogs_rejects_invalid_success_payload(payload: Any):
    """A successful response must still be the documented array shape."""
    fake = RecordingHttpClient(payload=payload)
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data")

    with pytest.raises(EditoStacApiRequestError) as raised:
        asyncio.run(client.get_user_catalogs("alice", access_token="token"))

    assert raised.value.status_code == 502
    assert raised.value.upstream_response == "EDITO STAC API returned an invalid response."


@unit
def test_get_user_catalogs_maps_connection_failure():
    """Transport failures become the shared upstream-unavailable error shape."""
    request = httpx.Request("GET", "https://stac.test/data/users/alice/catalogs")
    fake = RecordingHttpClient(error=httpx.ConnectError("no route", request=request))
    client = EditoStacApiClient(fake, edito_stac_api_url="https://stac.test/data")

    with pytest.raises(EditoStacApiUnavailableError):
        asyncio.run(client.get_user_catalogs("alice", access_token="token"))


@integration
def test_live_edito_stac_openapi_and_user_catalog_contract(
    live_edito_credentials,
    live_edito_token_pair,
):
    """The configured service exposes the operation and returns the typed array."""
    openapi_response = httpx.get(
        resolve_edito_stac_openapi_url(),
        headers={"Accept": "application/vnd.oai.openapi+json;version=3.0"},
        timeout=30.0,
        follow_redirects=True,
    )
    openapi_response.raise_for_status()
    operation = openapi_response.json()["paths"]["/users/{username}/catalogs"]["get"]
    assert operation["operationId"] == "UsersAPI::getUserCatalogs"

    async def fetch_catalogs() -> EditoStacUserCatalogs:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http_client:
            return await EditoStacApiClient(http_client).get_user_catalogs(
                live_edito_credentials.username,
                access_token=live_edito_token_pair.access_token,
            )

    catalogs = asyncio.run(fetch_catalogs())
    assert isinstance(catalogs.root, list)
