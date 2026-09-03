"""Public response models for the get-my-catalogs feature."""

from pydantic import RootModel


class GetMyCatalogsResponse(RootModel[list[str]]):
    """Catalog IDs available to the authenticated user."""


__all__ = ["GetMyCatalogsResponse"]
