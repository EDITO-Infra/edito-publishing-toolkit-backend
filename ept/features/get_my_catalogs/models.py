"""Public response models for the get-my-catalogs feature."""

from pydantic import RootModel


class GetMyCatalogsResponse(RootModel[list[str]]):
    """IDs of the EDITO STAC project catalogs owned by the authenticated user."""


__all__ = ["GetMyCatalogsResponse"]
