"""Centralized exception handling for the AI Fitness Coach API.

Provides custom exception classes for domain-specific errors and FastAPI
exception handlers that return consistent, user-friendly JSON error responses.

Usage in ``main.py``::

    from fast_api.app.core.errors import register_exception_handlers
    register_exception_handlers(app)
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


# ============================================================
# Custom exception classes
# ============================================================

class AppError(Exception):
    """Base application error with HTTP status and user-facing message."""

    status_code: int = 500
    detail: str = "An unexpected error occurred."
    error_code: str = "internal_error"

    def __init__(self, detail: str | None = None, status_code: int | None = None):
        if detail is not None:
            self.detail = detail
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.detail)


class LLMTimeoutError(AppError):
    """The upstream LLM did not respond within the allowed time window."""

    status_code = 504
    error_code = "llm_timeout"
    detail = "The AI model is taking too long to respond. Please try again in a moment."


class LLMServiceUnavailableError(AppError):
    """The upstream LLM provider returned 5xx or is unreachable."""

    status_code = 502
    error_code = "llm_unavailable"
    detail = (
        "The AI model is temporarily unavailable. A fallback response has been "
        "generated. Please try again shortly."
    )


class LLMRateLimitError(AppError):
    """The upstream LLM returned 429 — we are being rate-limited by the provider."""

    status_code = 429
    error_code = "llm_rate_limited"
    detail = "The AI service is experiencing high demand. Your request will be retried automatically."


class DatabaseError(AppError):
    """A database operation failed unexpectedly."""

    status_code = 503
    error_code = "database_error"
    detail = "A temporary database error occurred. Please try again."


class AuthenticationError(AppError):
    """The request lacks valid authentication credentials."""

    status_code = 401
    error_code = "authentication_required"
    detail = "Authentication is required for this endpoint."


class AuthorizationError(AppError):
    """The authenticated user does not have permission for this resource."""

    status_code = 403
    error_code = "forbidden"
    detail = "You do not have permission to access this resource."


class ResourceNotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = 404
    error_code = "not_found"
    detail = "The requested resource was not found."


class ServiceDegradedError(AppError):
    """A downstream service is degraded — the system is operating in fallback mode."""

    status_code = 200  # still 200 because we returned *something*
    error_code = "service_degraded"
    detail = ""


# ============================================================
# Error response helpers
# ============================================================

def _error_body(
    status_code: int,
    detail: str,
    error_code: str = "internal_error",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a consistent error response body."""
    body: dict[str, Any] = {
        "error": {
            "code": error_code,
            "status": status_code,
            "message": detail,
        }
    }
    if extra:
        body["error"].update(extra)
    return body


# ============================================================
# FastAPI exception handlers
# ============================================================

async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "AppError %s (status=%d): %s",
        exc.error_code, exc.status_code, exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.status_code, exc.detail, exc.error_code),
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle Starlette/FastAPI HTTPException with consistent format."""
    logger.info("HTTP %d: %s — %s %s", exc.status_code, exc.detail, request.method, request.url.path)

    # Map common status codes to error codes
    code_map = {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limit_exceeded",
    }
    error_code = code_map.get(exc.status_code, "http_error")

    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.status_code, str(exc.detail), error_code),
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with user-friendly messages."""
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        field = " → ".join(str(loc) for loc in error["loc"])
        errors.append({
            "field": field,
            "message": error["msg"],
            "type": error["type"],
        })

    logger.info("Validation error on %s %s: %d field(s)", request.method, request.url.path, len(errors))

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "validation_error",
                "status": 422,
                "message": "The request contains invalid data. Check the fields below.",
                "fields": errors,
            }
        },
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for unhandled exceptions — log full traceback, return generic 500."""
    logger.exception(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(
            500,
            "An unexpected internal error occurred. Our team has been notified.",
            "internal_error",
        ),
    )


# ============================================================
# Registration
# ============================================================

def register_exception_handlers(app: FastAPI) -> None:
    """Register all custom exception handlers on the FastAPI application.

    Call this after creating the app, before the first request.
    Handlers are registered in specificity order (subclass before base).
    """
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    logger.info("Registered centralized exception handlers.")
