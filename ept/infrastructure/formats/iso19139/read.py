"""ISO19139 format primitives: reading and parsing ISO metadata XML.

Primitives only — no scheme orchestration and no STAC mapping. Source/target
dispatch (S3, etc.) lives in the feature layer, which composes these with the
storage primitives in ``ept.infrastructure.storage.s3``.
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ept.infrastructure.utils.urlguard import require_public_http_url

logger = logging.getLogger(__name__)

__all__ = ["open_isoxml", "parse_isoxml"]

_FETCH_TIMEOUT_SECONDS = 30.0


def open_isoxml(source: str) -> str:
    """Read ISO19139 XML text from a local path or an http(s) URL.

    Returns the raw XML text; parsing is a separate primitive.
    """

    source = source.strip()
    if not source:
        raise ValueError("source is required.")

    scheme = urlsplit(source).scheme.lower()
    if scheme in {"http", "https"}:
        return _read_from_http(source)
    if scheme:
        raise ValueError(f"Unsupported ISO19139 source scheme: {scheme!r}")
    return _read_from_local(source)


def parse_isoxml(xml: str) -> tuple[ET.Element, dict[str, str]]:
    """Parse an ISO19139 record into (root element, namespace map).

    Namespaces are collected from the document header declarations; the
    common ISO19139 namespaces are seeded so records using a default
    namespace without a prefix still resolve.
    """

    root = ET.fromstring(xml)
    return root, _namespaces(xml)


def _namespaces(xml: str) -> dict[str, str]:
    ns: dict[str, str] = {}

    try:
        for _, item in ET.iterparse(io.StringIO(xml), events=("start-ns",)):
            prefix, uri = item
            if prefix and prefix not in ns:
                ns[prefix] = uri
    except Exception:
        pass

    ns.setdefault("gmd", "http://www.isotc211.org/2005/gmd")
    ns.setdefault("gco", "http://www.isotc211.org/2005/gco")
    ns.setdefault("gml", "http://www.opengis.net/gml")
    ns.setdefault("gml32", "http://www.opengis.net/gml/3.2")
    ns.setdefault("gmx", "http://www.isotc211.org/2005/gmx")
    ns.setdefault("xlink", "http://www.w3.org/1999/xlink")

    return ns


def _read_from_http(url: str) -> str:
    require_public_http_url(url)
    response = httpx.get(url, follow_redirects=True, timeout=_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    logger.info("Read ISO19139 record from HTTP url=%s bytes=%d", url, len(response.text))
    return response.text


def _read_from_local(path_value: str) -> str:
    path = Path(path_value)
    text = path.read_text(encoding="utf-8")
    logger.info("Read ISO19139 record from local path=%s bytes=%d", path, len(text))
    return text
