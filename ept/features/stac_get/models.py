"""Public request/response models for the STAC fetch feature."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from ept.infrastructure.utils.urlguard import require_public_http_url


class GetStacRequest(BaseModel):
    """Query parameters for fetching one STAC object by URL.

    Used as a FastAPI dependency so each field becomes a query parameter.
    """

    url: HttpUrl = Field(max_length=2048, description="Remote URL of the STAC object (JSON).")

    @field_validator("url")
    @classmethod
    def _reject_unsafe_fetch_target(cls, value: HttpUrl) -> HttpUrl:
        """SSRF boundary: refuse non-http(s), credentialed, or private-target URLs.

        Sync structural checks only — the DNS-level check runs at fetch time
        (``ept.infrastructure.utils.urlguard``), adjacent to the request.
        """
        require_public_http_url(str(value))
        return value

    model_config = ConfigDict(extra="forbid")


class GetStacResponse(BaseModel):
    """One STAC object fetched from a remote URL, wrapped for tooling."""

    source: str = Field(description="The URL the object was fetched from.")
    object_type: str = Field(description="Detected STAC object type: item, collection, catalog, item_collection, or unknown.")
    stac_object: dict[str, Any] = Field(description="The STAC object as JSON.")

    model_config = ConfigDict(extra="forbid")
