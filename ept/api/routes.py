"""Core service routes for the EPT API shell. Separate from dynamically loaded feature routes.
Allows tests on just the core API(ex. auth) without loading all feature routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field


class ApiIndex(BaseModel):
    """Root API index response."""

    service: str = Field(description="Human-readable service name.")
    docs: str = Field(description="Relative URL for the interactive OpenAPI documentation.")
    openapi: str = Field(description="Relative URL for the OpenAPI schema document.")


def register_service_routes(app: FastAPI) -> None:
    """Attach service metadata and documentation routes."""

    @app.get("/", include_in_schema=False)
    async def api_index_redirect() -> RedirectResponse:
        """Redirect the unversioned root to the versioned API index."""
        return RedirectResponse(url="/v1")

    @app.get(
        "/v1",
        response_model=ApiIndex,
        tags=["Service"],
        summary="Show the API index",
        operation_id="getApiIndexV1",
    )
    async def api_root() -> ApiIndex:
        """Return links to the API docs and OpenAPI schema."""
        return ApiIndex(
            service="EDITO Publishing Toolkit API",
            docs="/docs",
            openapi="/openapi.json",
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Return a lightweight process health response."""
        return {"status": "ok"}

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
        """Serve Swagger UI for the generated OpenAPI schema."""
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Swagger UI",
        )
