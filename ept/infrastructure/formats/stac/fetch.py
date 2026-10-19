"""Fetch STAC JSON documents from HTTP(S), s3:// URIs, or the local filesystem."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from ept.infrastructure.storage.s3 import is_s3_uri, s3_url_to_fs
from ept.infrastructure.utils.urlguard import require_public_http_url

__all__ = ["fetch_json"]


def fetch_json(uri: str, *, storage_options: dict[str, object] | None = None) -> dict[str, Any]:
    """Read one JSON document: http(s) URL (SSRF-guarded), s3:// URI, or local path."""
    if is_s3_uri(uri):
        fs, path = s3_url_to_fs(uri, storage_options=storage_options or {})
        with fs.open(path, "r") as handle:
            data = handle.read()
    elif uri.lower().startswith(("http://", "https://")):
        require_public_http_url(uri)
        response = httpx.get(uri, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        data = response.content
    else:
        data = Path(uri).read_bytes()
    document = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
    if not isinstance(document, dict):
        raise ValueError(f"'{uri}' does not contain a JSON object.")
    return document
