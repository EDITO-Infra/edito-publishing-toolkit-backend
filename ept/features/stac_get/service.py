"""Use-case logic for the STAC fetch feature."""

from __future__ import annotations

from typing import Any

import httpx

from ept.infrastructure.formats.stac import get_stac_object_type, read_stac


async def fetch_stac_object(
    url: str,
    *,
    http_client: httpx.AsyncClient,
    access_token: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Fetch one STAC object and return ``(stac_object, object_type)``.

    ``read_stac`` owns SSRF guarding (every redirect hop re-validated and
    host-pinned), retries, and throttling handling. ``access_token`` is
    attached only when the caller routes the EDITO STAC API through.
    """

    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    stac_object = await read_stac(url, client=http_client, headers=headers)
    return stac_object, get_stac_object_type(stac_object).value
