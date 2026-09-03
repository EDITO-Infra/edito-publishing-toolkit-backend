"""Public EPT request and response models for STAC publication."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class PublishStacRequest(BaseModel):
    """Body accepted by ``POST /v1/edito/stac/publish``.

    EPT owns this small public contract. The feature adapter adds the
    infrastructure catalog root and applies publication defaults.
    """

    model_config = ConfigDict(extra="forbid")

    # HttpUrl, not str: the publishing worker resolves anything that is not an
    # http(s) URL as a local filesystem path and reads it, so an unconstrained
    # string here lets a caller ask the worker to read its own files (for
    # example /proc/self/environ, which holds the worker's credentials).
    # Requiring an http(s) URL closes that at the EPT boundary. It does not
    # stop SSRF to internal HTTP targets: the worker follows redirects without
    # re-validating them, so that fix belongs upstream.
    remote_stac_url: Annotated[HttpUrl, Field(max_length=2048)] = Field(
        description="Remote http(s) URL of the STAC catalog to publish.",
        examples=[
            "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog.json",
            "https://minio.dive.edito.eu/oidc-myusername/project-id/catalog-1/catalog-2/catalog.json",
        ],
    )
    catalog_id: str = Field(
        min_length=10,
        max_length=137,
        pattern=r"^projects/[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$",
        description=(
            "catalog ID to publish STAC into, starting with `projects/`. "
            "To see your available catalogs, use the GET /v1/edito/stac/mycatalogs endpoint."
        ),
        examples=[
            "projects/project-id",
            "projects/project-id/catalog-1",
        ],
    )


class PublishStacResponse(BaseModel):
    """EPT response returned after a publication job is queued."""

    job_id: str = Field(description="Publication job identifier used for later lookup.")
    status: str = Field(description="Initial publication job status, normally ``queued``.")
