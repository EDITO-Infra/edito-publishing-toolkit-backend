"""Translate EDITO STAC user catalogs to the public feature response."""

from ept.infrastructure.services.edito_stac_api import EditoStacUserCatalogs

from .models import GetMyCatalogsResponse


def from_edito_stac_catalogs(
    catalogs: EditoStacUserCatalogs,
) -> GetMyCatalogsResponse:
    """Return catalog IDs rooted in the EDITO ``projects/`` namespace."""
    return GetMyCatalogsResponse(
        root=[catalog.id for catalog in catalogs.root if catalog.id.startswith("projects/")]
    )
