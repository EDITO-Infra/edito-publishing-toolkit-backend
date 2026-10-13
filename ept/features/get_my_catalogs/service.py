"""Use-case orchestration for retrieving an authenticated user's catalogs."""

from __future__ import annotations

from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, RootModel, ValidationError

from ept.infrastructure.services.editostacapi import (
    EditoStacApiClient,
    EditoStacApiRequestError,
)

from .models import GetMyCatalogsResponse


class EditoStacUserCatalog(BaseModel):
    """One record from ``GET /users/{username}/catalogs``."""

    model_config = ConfigDict(extra="allow")

    id: str


class EditoStacUserCatalogs(RootModel[list[EditoStacUserCatalog]]):
    """Array returned by the EDITO user-catalog endpoint."""


async def get_user_catalogs(
    *,
    username: str,
    access_token: str,
    http_client: httpx.AsyncClient,
) -> EditoStacUserCatalogs:
    """Fetch and validate the upstream catalog array for one EDITO username."""
    payload = await EditoStacApiClient(http_client).get_json(
        f"/users/{quote(username, safe='')}/catalogs",
        access_token=access_token,
    )
    try:
        return EditoStacUserCatalogs.model_validate(payload)
    except ValidationError as exc:
        # A malformed 2xx response has no upstream error status to preserve.
        raise EditoStacApiRequestError(502, "EDITO STAC API returned an invalid response.") from exc


async def get_my_catalogs(
    *,
    username: str,
    access_token: str,
    http_client: httpx.AsyncClient,
) -> GetMyCatalogsResponse:
    """Return the user's catalog IDs rooted in the EDITO ``projects/`` namespace."""
    catalogs = await get_user_catalogs(
        username=username,
        access_token=access_token,
        http_client=http_client,
    )
    return from_edito_stac_catalogs(catalogs)


def from_edito_stac_catalogs(catalogs: EditoStacUserCatalogs) -> GetMyCatalogsResponse:
    """Filter the upstream catalog array down to publishable ``projects/`` IDs."""
    return GetMyCatalogsResponse(
        root=[catalog.id for catalog in catalogs.root if catalog.id.startswith("projects/")]
    )
