"""HTTP(S) download primitive shared by features."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

__all__ = ["download_url"]


def download_url(url: str, dest: Path, *, attempts: int = 3, timeout: float = 600) -> Path:
    """Stream a URL to disk, retrying transient transport errors; no partial output."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
                response.raise_for_status()
                with dest.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
            return dest
        except httpx.TransportError as error:
            if attempt == attempts - 1:
                raise
            logger.warning("Retrying download after transport error (%s): %s", attempt + 1, error)
            time.sleep(2**attempt)
    raise RuntimeError("unreachable: retry loop always returns or raises")
