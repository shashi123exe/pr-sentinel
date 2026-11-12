"""Standardized application exceptions and FastAPI error handlers."""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.logging_config import get_current_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application exception for handled domain and API errors."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.headers = headers


def build_error_response(
    code: str,
    message: str,
    status_code: int,
    request: Request | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build standardized JSON error response following the PR Sentinel envelope."""
    request_id = "-"
    if request is not None and hasattr(request, "state"):
        req_state_id = getattr(request.state, "request_id", None)
        request_id = req_state_id or get_current_request_id()
    else:
        request_id = get_current_request_id()

    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle custom AppError exceptions."""
    logger.warning(
        "AppError [%s]: %s (status=%d)",
        exc.code,
        exc.message,
        exc.status_code,
    )
    return build_error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        request=request,
        headers=getattr(exc, "headers", None),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic request validation errors (HTTP 422)."""
    error_parts = []
    for err in exc.errors():
        loc_items = [str(item) for item in err.get("loc", []) if item != "body"]
        loc_str = " -> ".join(loc_items)
        msg = err.get("msg", "invalid value")
        error_parts.append(f"{loc_str}: {msg}" if loc_str else msg)

    summary_msg = "; ".join(error_parts) if error_parts else "Invalid request data"
    logger.info("RequestValidationError: %s", summary_msg)

    return build_error_response(
        code="validation_error",
        message=summary_msg,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        request=request,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI / Starlette HTTPException."""
    code_map = {
        status.HTTP_400_BAD_REQUEST: "bad_request",
        status.HTTP_401_UNAUTHORIZED: "unauthorized",
        status.HTTP_403_FORBIDDEN: "forbidden",
        status.HTTP_404_NOT_FOUND: "not_found",
        status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
        status.HTTP_409_CONFLICT: "conflict",
        status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
        status.HTTP_429_TOO_MANY_REQUESTS: "rate_limit_exceeded",
        status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_server_error",
        status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
    }
    code = code_map.get(exc.status_code, "http_error")
    detail = str(exc.detail) if exc.detail else "An HTTP error occurred"

    logger.warning("HTTPException [%d]: %s", exc.status_code, detail)
    return build_error_response(
        code=code,
        message=detail,
        status_code=exc.status_code,
        request=request,
        headers=exc.headers,
    )


async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Handle SlowAPI rate limit exceeded errors (HTTP 429)."""
    client_ip = request.client.host if request.client else "unknown"
    logger.warning("Rate limit exceeded for client: %s", client_ip)

    response = build_error_response(
        code="rate_limit_exceeded",
        message="Rate limit exceeded. Please wait before retrying.",
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        request=request,
    )

    # Inject rate limit headers (including Retry-After)
    if hasattr(request, "app") and hasattr(request.app.state, "limiter"):
        view_limit = getattr(request.state, "view_rate_limit", None)
        if view_limit is not None:
            response = request.app.state.limiter._inject_headers(response, view_limit)

    return response


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected server exceptions (HTTP 500) safely."""
    logger.exception("Unhandled server exception: %s", exc)
    return build_error_response(
        code="internal_server_error",
        message="An unexpected internal error occurred.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        request=request,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register all global error handlers with the FastAPI application."""
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
