"""RFC 9457 Problem Details models, helpers, and handlers
for the public API.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
from typing import Any

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ept.infrastructure.services.errors import UpstreamServiceRequestError, UpstreamServiceUnavailableError
from ept.infrastructure.utils.logging import sanitize_payload, sanitize_query_string


logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"
# This URL does not exist yet, but it is a stable prefix for public problem type URIs.
PROBLEM_BASE_URL = "https://api.edito-publishing-toolkit.org/problems"


class ProblemDetails(BaseModel):
    """RFC 9457 Problem Details response."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": f"{PROBLEM_BASE_URL}/authentication-required",
                    "title": "Authentication required",
                    "status": 401,
                    "detail": "Provide an Authorization header with a bearer access token.",
                    "reason": "missing_bearer_token",
                }
            ]
        }
    )

    type: str = Field(description="URI identifying this problem type.")
    title: str = Field(description="Short, human-readable problem summary.")
    status: int = Field(description="HTTP status code generated for this problem.")
    detail: str = Field(description="Human-readable explanation for this occurrence.")
    instance: str | None = Field(default=None, description="Request path for this occurrence.")
    reason: str | None = Field(default=None, description="Stable machine-readable reason code.")
    upstream_response: Any = Field(
        default=None,
        description="Sanitized upstream response when another EDITO service rejected the request.",
    )


class ValidationErrorItem(BaseModel):
    """One invalid request location in a validation problem."""

    detail: str = Field(description="Human-readable validation failure detail.")
    pointer: str = Field(description="JSON Pointer-style location for the invalid input.")


class ValidationProblemDetails(ProblemDetails):
    """Problem Details response with RFC-style validation errors."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": f"{PROBLEM_BASE_URL}/invalid-request",
                    "title": "Invalid request",
                    "status": 422,
                    "detail": "The request inputs did not match the API schema.",
                    "errors": [
                        {
                            "detail": "Invalid value.",
                            "pointer": "/field"
                        }
                    ],
                }
            ]
        }
    )

    errors: list[ValidationErrorItem] = Field(description="Invalid request locations.")


def problem_response(
    *,
    status_code: int,
    type_slug: str,
    title: str,
    detail: str,
    request: Request | None = None,
    reason: str | None = None,
    headers: dict[str, str] | None = None,
    upstream_response: Any = None,
) -> JSONResponse:
    """Return one RFC 9457 Problem Details JSON response."""
    body = ProblemDetails(
        type=f"{PROBLEM_BASE_URL}/{type_slug}",
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path if request else None,
        reason=reason,
        upstream_response=upstream_response,
    ).model_dump(exclude_none=True)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body),
        headers=headers,
        media_type=PROBLEM_JSON,
    )


def validation_problem_response(request: Request, errors: list[dict[str, Any]]) -> JSONResponse:
    """Translate FastAPI/Pydantic validation errors into Problem Details."""
    if _has_invalid_json_error(errors):
        body = ProblemDetails(
            type=f"{PROBLEM_BASE_URL}/invalid-json",
            title="Invalid JSON",
            status=422,
            detail="The request body must be valid JSON.",
            instance=request.url.path,
            reason="invalid_json",
        ).model_dump(exclude_none=True)
        return JSONResponse(status_code=422, content=jsonable_encoder(body), media_type=PROBLEM_JSON)

    invalid_params = [_validation_error_item(error) for error in errors]
    body = ValidationProblemDetails(
        type=f"{PROBLEM_BASE_URL}/invalid-request",
        title="Invalid request",
        status=422,
        detail=_validation_summary(errors),
        instance=request.url.path,
        reason="invalid_request",
        errors=invalid_params,
    ).model_dump(exclude_none=True)
    return JSONResponse(status_code=422, content=jsonable_encoder(body), media_type=PROBLEM_JSON)


def problem_responses(
    *,
    upstream_backed: bool = False,
    upstream_submission: bool = False,
    upstream_label: str = "Upstream service",
    upstream_unavailable_example: dict[str, Any] | None = None,
    upstream_error_example: dict[str, Any] | None = None,
    validation_examples: dict[str, Any] | None = None,
) -> dict[int | str, dict[str, Any]]:
    """Return common OpenAPI response metadata for protected feature routes.

    The API shell owns the generic Problem Details shapes, but individual
    features can pass service-specific examples. That keeps OpenAPI accurate for
    PublishingService-backed features without importing PublishingService
    classes into this module.
    """
    responses: dict[int | str, dict[str, Any]] = {
        401: _problem_response_doc(
            "Authentication problem",
            {
                "authentication_required": {
                    "summary": "Missing bearer token",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/authentication-required",
                        "title": "Authentication required",
                        "status": 401,
                        "detail": "Provide an Authorization header with a bearer access token.",
                        "reason": "missing_bearer_token",
                    },
                }
            },
        ),
        503: _problem_response_doc(
            "EDITO authentication service unavailable",
            {
                "auth_unavailable": {
                    "summary": "EDITO authentication service unavailable",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/authentication-service-unavailable",
                        "title": "Authentication service unavailable",
                        "status": 503,
                        "detail": "EDITO authentication service is unavailable.",
                        "reason": "auth_unavailable",
                    },
                }
            },
        ),
        422: _validation_response_doc(
            validation_examples
            or {
                "invalid_request": {
                    "summary": "Invalid request",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/invalid-request",
                        "title": "Invalid request",
                        "status": 422,
                        "detail": "The request inputs did not match the API schema.",
                        "errors": [
                            {
                                "detail": "Invalid value.",
                                "pointer": "/field"
                            }
                        ],
                    },
                }
            }
        ),
    }
    if upstream_submission:
        # Refresh-token auth is not globally required by the API. It is only
        # added to docs for routes that submit upstream jobs.
        responses[401]["content"][PROBLEM_JSON]["examples"]["missing_refresh_token"] = {
            "summary": "Missing refresh token",
            "value": {
                "type": f"{PROBLEM_BASE_URL}/refresh-token-required",
                "title": "Refresh token required",
                "status": 401,
                "detail": "Provide X-EDITO-Refresh-Token when submitting a publication or removal job.",
                "reason": "missing_refresh_token",
            },
        }
    if upstream_backed:
        # Feature routes can supply stable service-specific examples while the
        # response shape itself remains the generic API Problem Details model.
        unavailable_example = upstream_unavailable_example or upstream_service_unreachable_problem(
            type_slug="upstream-service-unreachable",
            title=f"{upstream_label} unreachable",
            detail=f"EPT could not reach the {upstream_label.lower()}.",
            reason="upstream_service_unreachable",
        )
        error_example = upstream_error_example or {
            "type": f"{PROBLEM_BASE_URL}/upstream-service-error-response",
            "title": f"{upstream_label} error response",
            "status": 502,
            "detail": f"The {upstream_label.lower()} returned an error response.",
            "reason": "upstream_service_error_response",
            "upstream_response": {"detail": "Upstream service error response."},
        }
        responses[502] = _problem_response_doc(
            f"{upstream_label} unavailable",
            {
                unavailable_example.get("reason", "upstream_service_unreachable"): {
                    "summary": unavailable_example.get("title", f"{upstream_label} unreachable"),
                    "value": unavailable_example,
                }
            },
        )
        responses["default"] = _problem_response_doc(
            f"{upstream_label} error response",
            {
                error_example.get("reason", "upstream_service_error_response"): {
                    "summary": error_example.get("title", f"{upstream_label} error response"),
                    "value": error_example,
                }
            },
        )
    return responses


def auth_problem_responses() -> dict[int | str, dict[str, Any]]:
    """Return OpenAPI response metadata for the public auth route."""
    return {
        401: _problem_response_doc(
            "Authentication failed",
            {
                "auth_failed": {
                    "summary": "EDITO authentication failed",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/authentication-failed",
                        "title": "Authentication failed",
                        "status": 401,
                        "detail": "EDITO authentication failed.",
                        "reason": "auth_failed",
                    },
                }
            },
        ),
        503: _problem_response_doc(
            "EDITO authentication service unavailable",
            {
                "auth_unavailable": {
                    "summary": "EDITO authentication service unavailable",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/authentication-service-unavailable",
                        "title": "Authentication service unavailable",
                        "status": 503,
                        "detail": "EDITO authentication service is unavailable.",
                        "reason": "auth_unavailable",
                    },
                }
            },
        ),
        422: _validation_response_doc(
            {
                "invalid_grant": {
                    "summary": "Invalid token grant",
                    "value": {
                        "type": f"{PROBLEM_BASE_URL}/invalid-request",
                        "title": "Invalid request",
                        "status": 422,
                        "detail": "The request body did not match the API schema.",
                        "errors": [
                            {
                                "detail": 'grant_type is required and must be "password" or "refresh_token".',
                                "pointer": "/",
                            }
                        ],
                    },
                }
            }
        ),
    }


def upstream_service_unreachable_problem(*, type_slug: str, title: str, detail: str, reason: str) -> dict[str, Any]:
    """Return a public upstream-service-unreachable problem body.

    Concrete infrastructure adapters decide the service-specific slug, title,
    and reason. The API layer only assembles the standard RFC 9457 fields.
    """
    return {
        "type": f"{PROBLEM_BASE_URL}/{type_slug}",
        "title": title,
        "status": 502,
        "detail": detail,
        "reason": reason,
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Map generic API failures to stable, sanitized API responses."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        """Convert FastAPI HTTPException values into EPT Problem Details."""
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        reason = detail.get("reason") if isinstance(detail.get("reason"), str) else None
        message = detail.get("message") if isinstance(detail.get("message"), str) else str(exc.detail)
        return problem_response(
            status_code=exc.status_code,
            type_slug=_problem_slug(reason, exc.status_code),
            title=_problem_title(reason, exc.status_code),
            detail=message,
            request=request,
            reason=reason,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Convert request validation failures into EPT validation problems."""
        logger.warning(
            "API request validation failed method=%s path=%s query=%s errors=%s body=%s",
            request.method,
            request.url.path,
            sanitize_query_string(request.url.query) or "-",
            sanitize_payload(exc.errors()),
            sanitize_payload(exc.body),
        )
        return validation_problem_response(request, exc.errors())

    @app.exception_handler(UpstreamServiceUnavailableError)
    async def upstream_unavailable(
        request: Request,
        exc: UpstreamServiceUnavailableError,
    ) -> JSONResponse:
        """Return a stable 502 when an upstream service cannot be reached."""
        logger.warning(
            "Upstream service unavailable service=%s method=%s path=%s reason=%s",
            exc.service_label,
            request.method,
            request.url.path,
            exc.reason,
        )
        return JSONResponse(
            status_code=502,
            content=upstream_service_unreachable_problem(
                type_slug=exc.type_slug,
                title=exc.title,
                detail=exc.detail,
                reason=exc.reason,
            )
            | {"instance": request.url.path},
            media_type=PROBLEM_JSON,
        )

    @app.exception_handler(UpstreamServiceRequestError)
    async def upstream_error_response(
        request: Request,
        exc: UpstreamServiceRequestError,
    ) -> JSONResponse:
        """Return a sanitized upstream error response."""
        logger.warning(
            "Upstream service returned an error response service=%s method=%s path=%s reason=%s status_code=%s response=%s",
            exc.service_label,
            request.method,
            request.url.path,
            exc.reason,
            exc.status_code,
            sanitize_payload(exc.upstream_response),
        )
        return problem_response(
            status_code=exc.status_code,
            type_slug=exc.type_slug,
            title=exc.title,
            detail=exc.public_detail,
            request=request,
            reason=exc.reason,
            headers=exc.response_headers,
            upstream_response=exc.upstream_response,
        )


def _problem_response_doc(description: str, examples: dict[str, Any]) -> dict[str, Any]:
    """Build OpenAPI metadata for a Problem Details response."""
    return {
        "model": ProblemDetails,
        "description": description,
        "content": {PROBLEM_JSON: {"examples": examples}},
    }


def _validation_response_doc(examples: dict[str, Any]) -> dict[str, Any]:
    """Build OpenAPI metadata for a validation Problem Details response."""
    return {
        "model": ValidationProblemDetails,
        "description": "Request validation problem",
        "content": {PROBLEM_JSON: {"examples": examples}},
    }


def _has_invalid_json_error(errors: list[dict[str, Any]]) -> bool:
    """Return whether FastAPI reported malformed JSON input."""
    return any(str(error.get("type", "")).startswith("json_invalid") for error in errors)


def _validation_error_item(error: dict[str, Any]) -> ValidationErrorItem:
    """Convert one Pydantic error item into EPT's public validation shape."""
    error_type = str(error.get("type", ""))
    return ValidationErrorItem(
        detail=_public_validation_message(error_type, str(error.get("msg", "Invalid value."))),
        pointer=_json_pointer(error.get("loc", ())),
    )


def _public_validation_message(error_type: str, fallback: str) -> str:
    """Return a stable validation message safe to expose to API callers."""
    if error_type == "missing":
        return "Field is required."
    if error_type == "extra_forbidden":
        return "Field is not supported."
    if error_type.startswith("string_too_short"):
        return "Value must not be empty."
    if error_type in {"union_tag_not_found", "union_tag_invalid"}:
        return 'grant_type is required and must be "password" or "refresh_token".'
    if error_type.startswith("value_error"):
        return fallback.removeprefix("Value error, ")
    return fallback


def _validation_summary(errors: list[dict[str, Any]]) -> str:
    """Summarize a validation problem for the top-level Problem Details body."""
    locations = {_validation_location(error.get("loc", ())) for error in errors}
    if locations == {"body"} and any(error.get("type") == "missing" for error in errors):
        return "The request body is missing required fields."
    if locations == {"query"} and any(error.get("type") == "missing" for error in errors):
        return "The request query parameters are missing required values."
    labels = [_location_label(location) for location in _ordered_locations(locations)]
    if not labels:
        return "The request inputs did not match the API schema."
    return f"The request {_format_location_labels(labels)} did not match the API schema."


def _validation_location(location: Any) -> str:
    """Return the broad request input location from one validation error."""
    if isinstance(location, (list, tuple)) and location:
        raw_location = str(location[0])
        if raw_location in {"body", "query", "path", "header", "cookie"}:
            return raw_location
    return "input"


def _ordered_locations(locations: set[str]) -> list[str]:
    """Return validation locations in stable public-response order."""
    order = ["body", "query", "path", "header", "cookie", "input"]
    return [location for location in order if location in locations]


def _location_label(location: str) -> str:
    """Return public text for one broad validation location."""
    return {
        "body": "body",
        "query": "query parameters",
        "path": "path parameters",
        "header": "headers",
        "cookie": "cookies",
    }.get(location, "inputs")


def _format_location_labels(labels: list[str]) -> str:
    """Join location labels into a compact English phrase."""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return f"{', '.join(labels[:-1])}, and {labels[-1]}"


def _json_pointer(location: Any) -> str:
    """Translate a FastAPI/Pydantic location tuple into a JSON Pointer."""
    if not isinstance(location, (list, tuple)):
        return "/"
    raw_parts = list(location)
    if raw_parts and raw_parts[0] in {"body", "query", "path"}:
        raw_parts = raw_parts[1:]
    parts = [str(part) for part in raw_parts if not isinstance(part, int)]
    if not parts:
        return "/"
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def title_for_status(status_code: int) -> str:
    """Return a stable title for generic HTTP status codes."""
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"


def _problem_slug(reason: str | None, status_code: int) -> str:
    """Map a stable reason code to a public Problem Details type slug."""
    match reason:
        case "missing_bearer_token":
            return "authentication-required"
        case "invalid_bearer_token":
            return "invalid-bearer-token"
        case "missing_refresh_token":
            return "refresh-token-required"
        case "auth_failed":
            return "authentication-failed"
        case "auth_unavailable":
            return "authentication-service-unavailable"
        case "auth_response_missing_tokens":
            return "authentication-response-invalid"
        case "auth_response_invalid_json":
            return "authentication-response-invalid"
        case "auth_config_invalid":
            return "authentication-config-invalid"
        case _:
            return title_for_status(status_code).lower().replace(" ", "-")


def _problem_title(reason: str | None, status_code: int) -> str:
    """Map a stable reason code to a public Problem Details title."""
    match reason:
        case "missing_bearer_token":
            return "Authentication required"
        case "invalid_bearer_token":
            return "Invalid bearer token"
        case "missing_refresh_token":
            return "Refresh token required"
        case "auth_failed":
            return "Authentication failed"
        case "auth_unavailable":
            return "Authentication service unavailable"
        case "auth_response_missing_tokens" | "auth_response_invalid_json":
            return "Authentication response invalid"
        case "auth_config_invalid":
            return "Authentication configuration invalid"
        case _:
            return title_for_status(status_code)
