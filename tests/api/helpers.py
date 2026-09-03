"""Small helpers for in-process EPT API tests."""

from __future__ import annotations

from fastapi import Depends, FastAPI
import httpx
from pydantic import BaseModel

from ept import __version__
from ept.api.app import register_openapi_schema, register_request_logging
from ept.api.auth import router as auth_router
from ept.api.dependencies import require_edito_bearer_auth
from ept.api.errors import problem_responses, register_exception_handlers
from ept.api.main import app as full_app
from ept.api.routes import register_service_routes
from ept.infrastructure.services.edito_auth import EditoBearerAuth


class ProtectedPayload(BaseModel):
    """Small request body used by API-only auth and validation tests."""

    value: str


class ProtectedResponse(BaseModel):
    """Small response body used by API-only auth and OpenAPI tests."""

    accepted: bool


def create_api_shell_test_app() -> FastAPI:
    """Create an API-shell app without loading registry-discovered features.
    Allows tests to exercise the API shell and OpenAPI schema without loading any
    feature modules."""
    app = FastAPI(
        title="EDITO Publishing Toolkit API",
        description="API shell test app.",
        version=__version__,
        docs_url=None,
    )
    register_request_logging(app)
    register_exception_handlers(app)
    register_service_routes(app)
    app.include_router(auth_router)

    @app.post(
        "/v1/protected",
        response_model=ProtectedResponse,
        responses=problem_responses(upstream_backed=True),
        tags=["Test"],
        summary="Exercise protected API behavior",
        operation_id="exerciseProtectedApiBehaviorV1",
    )
    async def protected_route(
        _payload: ProtectedPayload,
        _auth: EditoBearerAuth = Depends(require_edito_bearer_auth),
    ) -> ProtectedResponse:
        return ProtectedResponse(accepted=True)

    register_openapi_schema(app)
    return app


async def get_many(
    *paths: str,
    app: FastAPI = full_app,
    follow_redirects: bool = True,
) -> list[httpx.Response]:
    """GET several API paths using one in-memory FastAPI client."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=follow_redirects,
    ) as client:
        return [await client.get(path) for path in paths]


async def request(
    method: str,
    path: str,
    *,
    app: FastAPI = full_app,
    **kwargs,
) -> httpx.Response:
    """Send one in-memory request to the EPT API."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, **kwargs)
