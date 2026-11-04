"""Public request/response models for the validate-STAC feature.

``ValidateStacRequest`` documents ``stac_url`` as the only field users need.
The remaining fields are accepted overrides: they stay in the request schema
but the model-level example shown in Swagger UI contains only ``stac_url``,
so they are never surfaced as a suggested/example input.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, field_validator

from ept.infrastructure.formats.stac import StacObjectType
from ept.infrastructure.utils.urlguard import require_public_http_url


class ValidationLevel(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ValidationIssue(BaseModel):
    """A single validation finding."""
    level: ValidationLevel
    object_type: StacObjectType
    object_id: str | None = None
    location: str = Field(..., description="URL or JSON Pointer path")
    rule: str = Field(..., description="Rule ID, e.g., 'stac-core-schema', 'edito_collection_in_collection'")
    message: str

    model_config = {"extra": "forbid"}


class ValidationTreeNode(BaseModel):
    """Recursive tree structure for the validation report.

    ``object_is_valid`` reflects this object only — it does not propagate from
    children; a valid collection stays valid even if its items are invalid.
    """
    url: str
    object_type: StacObjectType
    object_id: str | None = None
    object_is_valid: bool
    children: list["ValidationTreeNode"] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)


class ValidateStacRequest(BaseModel):
    stac_url: HttpUrl = Field(..., max_length=2048, description="Remote URL to the STAC object")

    @field_validator("stac_url")
    @classmethod
    def _reject_unsafe_fetch_target(cls, value: HttpUrl) -> HttpUrl:
        """SSRF boundary: refuse non-http(s), credentialed, or private-target URLs.

        Sync structural checks only — the DNS-level check runs at fetch time
        (``ept.infrastructure.utils.urlguard``), adjacent to the request.
        """
        require_public_http_url(str(value))
        return value

    validate_children: bool = Field(
        default=True,
        description="If true, recursively validate linked collections and items",
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum recursion depth for child validation",
    )
    max_objects: int = Field(
        default=5000,
        ge=1,
        description="Maximum number of STAC objects (Catalogs, Collections, "
                    "Items in total) to validate",
    )

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {"stac_url": "https://minio.dive.edito.eu/oidc-myusername/"
                    "project-id/catalog-1/catalog.json"}
            ]
        },
    }


class ObjectTypeSummary(BaseModel):
    """Valid/invalid counts for one STAC object type."""

    total: int
    valid: int
    invalid: int


class ValidationSummary(BaseModel):
    """Top-level run summary shown before the detailed issues."""

    total_objects: int
    valid_objects: int
    invalid_objects: int
    by_type: dict[StacObjectType, ObjectTypeSummary]
    error_count: int
    warning_count: int
    # rule slug → number of errors with that rule, sorted descending;
    # gives the dominant failure modes at a glance
    errors_by_rule: dict[str, int]

    model_config = {"extra": "forbid"}


class ValidateStacResponse(BaseModel):
    root_url: str
    root_type: StacObjectType
    # Run-level: true iff no ERROR-level issues anywhere in the tree
    # (root included). Warnings — e.g. traversal_limit — do not invalidate.
    stac_is_valid: bool
    total_objects_checked: int
    summary: ValidationSummary
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    validation_tree: ValidationTreeNode

    model_config = {"extra": "forbid"}
