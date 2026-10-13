"""SSRF guard for outbound fetches whose URL derives from user input.

Features like validate-STAC fetch arbitrary caller-supplied URLs, which makes
every request a potential server-side-request-forgery vector: a URL pointing
at loopback, private, or link-local space (cloud metadata, internal services)
would be fetched by EPT and its response partially echoed back through
validation output. The guard is applied in two stages:

1. **Boundary** — :func:`require_public_http_url` is a sync structural check
   (scheme allowlist, embedded credentials, non-public literal IPs) designed
   to run inside pydantic field validators, so bad URLs are rejected with a
   422 before any fetch code runs. It returns the validated URL.
2. **Fetch site** — :func:`public_http_url` adds the DNS check that is only
   possible with I/O (a public-looking hostname may resolve to a private
   address) and returns the validated URL. Called immediately before every
   outbound request, including each manual redirect hop — redirects are
   followed one request at a time precisely so every hop is re-checked here.

Both raise :class:`UnsafeUrlError`. Public-address policy uses
``ipaddress``'s ``is_global``, which also covers non-``is_private`` ranges
like CGNAT 100.64.0.0/10.
"""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeUrlError(ValueError):
    """A URL was rejected by the guard before fetching."""


async def _resolve(host: str) -> list[str]:
    """Resolve ``host`` to all its address strings; UnsafeUrlError on failure."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError as exc:
        raise UnsafeUrlError(f"Host {host!r} could not be resolved") from exc
    return [info[4][0] for info in infos]


def _reject_non_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if not ip.is_global:
        raise UnsafeUrlError(
            "URL host resolves to a non-public address; refusing to fetch it"
        )


def require_public_http_url(url: str) -> str:
    """Return ``url`` after structural validation; raise :class:`UnsafeUrlError` if unsafe.

    Sync checks only — safe to run inside pydantic field validators.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(
            f"URL scheme must be http or https, got {parts.scheme!r}"
        )
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("URLs with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("URL has no hostname")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None  # not a literal IP — DNS check happens in the async path
    if literal is not None:
        _reject_non_public(literal)
    return url


def _host_and_port(url: str) -> tuple[str, int | None]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = None  # invalid port — treated as unset; the fetch will fail anyway
    return (parts.hostname or "", port)


def same_http_host(a: str, b: str) -> bool:
    """True when both URLs target the same hostname and explicit port.

    Scheme-agnostic by design: an http→https upgrade on the same host is a
    benign redirect, while a hop to a different host is exactly the traversal
    escape this check exists to stop.
    """
    return _host_and_port(a) == _host_and_port(b)


async def public_http_url(url: str) -> str:
    """Return ``url`` after full validation; raise :class:`UnsafeUrlError` if unsafe.

    Structural checks plus DNS: every address the host resolves to must be
    globally routable. Call immediately before every outbound request,
    including each manual redirect hop.
    """
    url = require_public_http_url(url)
    host = urlsplit(url).hostname
    if host is None:  # unreachable: require_public_http_url guarantees a hostname
        raise UnsafeUrlError("URL has no hostname")

    try:
        ipaddress.ip_address(host)
        return url  # literal IP — already rejected above if non-public
    except ValueError:
        pass  # hostname — resolve below

    for address in await _resolve(host):
        _reject_non_public(ipaddress.ip_address(address))
    return url
