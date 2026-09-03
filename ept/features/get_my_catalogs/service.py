"""Use-case orchestration for retrieving an authenticated user's catalogs."""

from __future__ import annotations

import logging

from ept.infrastructure.services.edito_stac_api import EditoStacApiClient

from .adapters import from_edito_stac_catalogs
from .models import GetMyCatalogsResponse


logger = logging.getLogger(__name__)


async def get_my_catalogs(
    *,
    username: str,
    access_token: str,
    edito_stac_client: EditoStacApiClient,
) -> GetMyCatalogsResponse:
    """Adapt the user's EDITO catalogs to EPT's catalog-ID response."""
    logger.info("Starting get_my_catalogs username=%s", username)
    catalogs = await edito_stac_client.get_user_catalogs(
        username,
        access_token=access_token,
    )
    response = from_edito_stac_catalogs(catalogs)
    logger.info(
        "Completed get_my_catalogs username=%s upstream_count=%s returned_count=%s",
        username,
        len(catalogs.root),
        len(response.root),
    )
    return response
