"""Tests for the SSRF URL guard and its use in the STAC fetch primitives.

``assert_public_http_url`` is an infrastructure primitive
(``ept.infrastructure.utils.urlguard``); ``read_stac`` applies it to every
request, including each manually-followed redirect hop.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

import ept.infrastructure.formats.stac.read as stac_read
import ept.infrastructure.utils.urlguard as urlguard
from ept.infrastructure.formats.stac import read_stac
from ept.infrastructure.utils.urlguard import (
    UnsafeUrlError,
    public_http_url,
    same_http_host,
)
from tests.conftest import unit


def _guard(url: str) -> str:
    return asyncio.run(public_http_url(url))


@unit
def test_rejects_non_http_schemes():
    """file://, ftp://, data: and friends must never reach the HTTP client."""
    for url in (
        "file:///etc/passwd",
        "ftp://example.com/catalog.json",
        "data:text/plain,hello",
    ):
        with pytest.raises(UnsafeUrlError):
            _guard(url)


@unit
def test_rejects_embedded_credentials():
    with pytest.raises(UnsafeUrlError):
        _guard("https://user:secret@example.com/catalog.json")


@unit
def test_rejects_private_and_local_ip_literals():
    """Loopback, private, link-local (cloud metadata), and unique-local hosts."""
    for url in (
        "http://127.0.0.1:8000/catalog.json",
        "http://10.0.0.1/catalog.json",
        "http://172.16.5.4/catalog.json",
        "http://192.168.1.10/catalog.json",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/catalog.json",
        "http://[fd00::1]/catalog.json",
        "http://0.0.0.0/catalog.json",
    ):
        with pytest.raises(UnsafeUrlError):
            _guard(url)


@unit
def test_rejects_public_literal_ip_with_credentials():
    with pytest.raises(UnsafeUrlError):
        _guard("https://user@1.1.1.1/catalog.json")


@unit
def test_accepts_public_ip_literals():
    assert _guard("https://1.1.1.1/catalog.json") == "https://1.1.1.1/catalog.json"
    _guard("http://8.8.8.8/x")
    _guard("http://[2606:4700::1111]/x")


@unit
def test_hostname_resolving_to_private_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """A public-looking hostname must not smuggle a private address past DNS."""

    async def fake_resolve(host: str) -> list[str]:
        return ["169.254.169.254"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    with pytest.raises(UnsafeUrlError):
        _guard("https://internal.example.com/catalog.json")


@unit
def test_hostname_resolving_to_public_is_accepted(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    assert _guard("https://example.com/catalog.json") == "https://example.com/catalog.json"


@unit
def test_unresolvable_hostname_is_rejected(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve(host: str) -> list[str]:
        raise UnsafeUrlError(f"Host {host!r} could not be resolved")

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    with pytest.raises(UnsafeUrlError):
        _guard("https://does-not-exist.example.com/catalog.json")


# ─── same_http_host (traversal/redirect host pin) ─────────────────────────


@unit
def test_same_http_host():
    assert same_http_host("https://h.example.com/a.json", "https://h.example.com/b.json")
    # scheme upgrade on the same host is allowed (benign http→https redirect)
    assert same_http_host("http://h.example.com/a", "https://h.example.com/b")
    assert same_http_host("https://h.example.com:8443/a", "https://h.example.com:8443/b")
    assert not same_http_host("https://h.example.com/a", "https://other.example.com/b")
    assert not same_http_host("https://h.example.com/a", "https://h.example.com:8443/a")


# ─── read_stac integration with the guard ─────────────────────────────────


@unit
def test_read_stac_rejects_private_url_without_requesting():
    """The guard fires before any HTTP request is made."""
    requested = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested += 1
        return httpx.Response(200, json={"ok": True})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await read_stac("http://169.254.169.254/latest/meta-data/", client=client)

    with pytest.raises(UnsafeUrlError):
        asyncio.run(run())
    assert requested == 0


@unit
def test_read_stac_rejects_redirect_into_private_space(
    monkeypatch: pytest.MonkeyPatch,
):
    """A public URL that 302s into loopback must be blocked at the redirect hop."""

    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "http://127.0.0.1:8000/steal"}
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await read_stac("https://evil.example.com/catalog.json", client=client)

    with pytest.raises(UnsafeUrlError):
        asyncio.run(run())
    # Only the initial public request was made; the loopback hop never was.
    assert requested == ["https://evil.example.com/catalog.json"]


@unit
def test_read_stac_rejects_cross_host_redirect(monkeypatch: pytest.MonkeyPatch):
    """A redirect to a different (public) host is rejected — the catalog
    cannot move the traversal to a host the caller never asked about."""

    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302, headers={"Location": "https://other.example.com/catalog.json"}
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await read_stac("https://stac.example.com/catalog.json", client=client)

    with pytest.raises(UnsafeUrlError):
        asyncio.run(run())
    assert requested == ["https://stac.example.com/catalog.json"]


@unit
def test_read_stac_follows_same_host_redirect(monkeypatch: pytest.MonkeyPatch):
    """Redirects that stay on the requested host (e.g. path moves) still work."""

    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/catalog.json":
            return httpx.Response(
                301, headers={"Location": "https://stac.example.com/moved/catalog.json"}
            )
        return httpx.Response(200, json={"type": "Catalog", "id": "c"})

    async def run() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await read_stac(
                "https://stac.example.com/catalog.json", client=client
            )

    data = asyncio.run(run())
    assert data["id"] == "c"


@unit
def test_read_stac_gives_up_after_redirect_hop_limit(monkeypatch: pytest.MonkeyPatch):
    """A redirect loop between public URLs terminates in TooManyRedirects."""

    async def fake_resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(urlguard, "_resolve", fake_resolve)
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"Location": "https://a.example.com/x"})

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await read_stac("https://a.example.com/x", client=client)

    with pytest.raises(httpx.TooManyRedirects):
        asyncio.run(run())
    assert requests == stac_read._MAX_REDIRECT_HOPS + 1
