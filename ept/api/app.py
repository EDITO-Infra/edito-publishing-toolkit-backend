"""FastAPI application factory and process-level API wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import os
from pathlib import Path
from queue import Queue
import time
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi

from ept import __version__
from ept.infrastructure.utils.logging import sanitize_query_string

from .auth import router as auth_router
from .errors import PROBLEM_JSON, register_exception_handlers
from .router_loader import include_feature_routers
from .routes import register_service_routes


logger = logging.getLogger(__name__)
_log_listener: QueueListener | None = None


def configure_logging() -> QueueListener:
    """Start non-blocking console and rotating file logging for the API process."""
    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_path = Path(os.getenv("API_LOG", "logs/api.log"))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    log_queue: Queue = Queue(-1)
    queue_handler = QueueHandler(log_queue)
    queue_handler.setLevel(log_level)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(queue_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )

    listener = QueueListener(
        log_queue,
        console_handler,
        file_handler,
        respect_handler_level=True,
    )
    listener.start()
    return listener


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start and stop process-level resources."""
    global _log_listener

    _log_listener = configure_logging()
    logger.info("EPT API logging started.")

    try:
        yield
    finally:
        logger.info("EPT API shutting down.")
        if _log_listener is not None:
            _log_listener.stop()
            _log_listener = None


def create_app() -> FastAPI:
    """Create the registry-driven EPT API application."""
    app = FastAPI(
        title="EDITO Publishing Toolkit API",
        description="Registry-driven EDITO publication capabilities.",
        version=__version__,
        docs_url=None,
        lifespan=lifespan,
        openapi_tags=[
            {
                "name": "Service",
                "description": "Application metadata and documentation endpoints.",
            },
            {
                "name": "Authentication",
                "description": "EDITO authentication and token refresh endpoints.",
            },
            {
                "name": "STAC Publication and Removal",
                "description": "Queue STAC publication and removal jobs.",
            },
            {
                "name": "Publication Jobs",
                "description": "Read publication job state and logs.",
            },
            {
                "name": "STAC Catalogs",
                "description": "Discover EDITO catalogs available to the authenticated user.",
            },
        ],
    )

    register_request_logging(app)
    register_exception_handlers(app)
    register_service_routes(app)

    app.include_router(auth_router)

    # Feature routers declare their own auth dependencies at the route boundary.
    include_feature_routers(app)

    register_openapi_schema(app)

    return app


def register_openapi_schema(app: FastAPI) -> None:
    """Install OpenAPI-only documentation fixes that do not change runtime auth."""

    def custom_openapi() -> dict[str, Any]:
        """Build and cache the OpenAPI schema with EPT-specific documentation fixes."""
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        _document_bearer_jwt(schema)
        _document_auth_request_example(schema)
        _document_problem_json_responses(schema)
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _document_bearer_jwt(schema: dict[str, Any]) -> None:
    """Mark FastAPI's bearer security scheme as JWT bearer auth."""
    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    bearer = security_schemes.get("HTTPBearer")
    if isinstance(bearer, dict):
        bearer["bearerFormat"] = "JWT"




def _document_auth_request_example(schema: dict[str, Any]) -> None:
    """Provide the default password-grant body shown by API documentation clients."""
    auth_json = (
        schema.get("paths", {})
        .get("/v1/auth", {})
        .get("post", {})
        .get("requestBody", {})
        .get("content", {})
        .get("application/json")
    )
    if isinstance(auth_json, dict):
        auth_json["example"] = {
            "grant_type": "password",
            "username": "YOUR_EDITO_USERNAME",
            "password": "YOUR_EDITO_PASSWORD",
        }


def _document_problem_json_responses(schema: dict[str, Any]) -> None:
    """Keep documented error responses on Problem Details media type only."""
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for status_code, response in operation.get("responses", {}).items():
                if status_code != "default" and not str(status_code).startswith(("4", "5")):
                    continue
                content = response.get("content")
                if not isinstance(content, dict):
                    continue
                if PROBLEM_JSON in content:
                    problem_content = content[PROBLEM_JSON]
                    content.clear()
                    content[PROBLEM_JSON] = problem_content


def register_request_logging(app: FastAPI) -> None:
    """Log request lifecycle details without exposing query-string credentials."""

    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Log one request/response lifecycle without exposing sensitive query values."""
        started_at = time.perf_counter()
        query = sanitize_query_string(request.url.query) or "-"
        client = request.client.host if request.client else "unknown"

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "API request failed method=%s path=%s query=%s client=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                query,
                client,
                duration_ms,
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        if (
            request.method == "GET"
            and request.url.path in {"/", "/v1", "/healthz"}
            and not request.url.query
            and response.status_code < 400
        ):
            return response
        logger.log(
            logging.WARNING if response.status_code >= 400 else logging.INFO,
            "API request completed method=%s path=%s query=%s client=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            query,
            client,
            response.status_code,
            duration_ms,
        )
        return response


app = create_app()
