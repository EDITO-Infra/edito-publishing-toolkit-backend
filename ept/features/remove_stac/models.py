"""Public EPT request and response models for project-catalog removal."""

from pydantic import BaseModel, ConfigDict, Field


class RemoveStacRequest(BaseModel):
    """Body accepted by ``POST /v1/edito/stac/remove``."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(
        min_length=10,
        max_length=137,
        pattern=r"^projects/[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$",
        description=(
            "catalog ID to be removed starting with `projects/`. "
            "To see your available catalogs, see feature `GET /v1/edito/stac/mycatalogs`. "
        ),
        examples=[
            "projects/project-id/catalog-1",
            "projects/project-id/catalog-1/catalog-2",
        ],
    )


class RemoveStacResponse(BaseModel):
    """EPT response returned after a removal job is queued."""

    job_id: str = Field(description="Removal job identifier used for later lookup.")
    status: str = Field(description="Initial removal job status, normally ``queued``.")
