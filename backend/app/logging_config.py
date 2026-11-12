"""Structured logging configuration, secret redaction, and request ID middleware."""

import contextvars
import logging
import re
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Context variable to hold the current request ID across async tasks
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="-",
)


def get_current_request_id() -> str:
    """Return the current request ID from context or a placeholder."""
    return request_id_ctx.get()


# Patterns for sensitive credentials
REDACTION_PATTERNS = [
    # GitHub classic PAT: ghp_...
    (re.compile(r"ghp_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    # GitHub fine-grained PAT: github_pat_...
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    # Google API Key: AIza...
    (re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "[REDACTED_GEMINI_KEY]"),
    # Bearer tokens (Authorization headers / bearer tokens)
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED_TOKEN]"),
    # Generic Authorization headers (e.g. basic, token, secret)
    (
        re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:[a-z]+\s+)?[^\s,;]+"),
        r"\1[REDACTED_AUTH]",
    ),
]


def redact_sensitive_text(text: str) -> str:
    """Apply all redaction rules to a given string."""
    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive tokens and injects request ID."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Inject request_id if not already present
        if not hasattr(record, "request_id"):
            record.request_id = get_current_request_id()

        # Redact the log message if it is a string
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)

        def _redact_obj(obj: Any) -> Any:
            if isinstance(obj, str):
                return redact_sensitive_text(obj)
            if isinstance(obj, dict):
                return {k: _redact_obj(v) for k, v in obj.items()}
            if isinstance(obj, tuple):
                return tuple(_redact_obj(v) for v in obj)
            if isinstance(obj, list):
                return [_redact_obj(v) for v in obj]
            return obj

        # Redact any tuple or dict arguments passed to the logger
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(_redact_obj(arg) for arg in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: _redact_obj(v) for k, v in record.args.items()}

        if record.exc_text:
            record.exc_text = redact_sensitive_text(record.exc_text)
        if getattr(record, "stack_info", None):
            record.stack_info = redact_sensitive_text(record.stack_info)

        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that ensures all output, including tracebacks, is redacted."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_sensitive_text(formatted)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware that generates and attaches a unique request ID to each request."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # Generate a new UUID4 for this request
        req_id = str(uuid.uuid4())
        token = request_id_ctx.set(req_id)
        request.state.request_id = req_id

        try:
            response: Response = await call_next(request)
        finally:
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = req_id
        return response


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging with redaction filter and request ID formatting."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = RedactingFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(RedactingFilter())

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers on re-initialization
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        root_logger.addHandler(handler)
    else:
        # Ensure our redacting filter is attached to all existing stream handlers
        for h in root_logger.handlers:
            h.setLevel(level)
            h.setFormatter(formatter)
            if not any(isinstance(f, RedactingFilter) for f in h.filters):
                h.addFilter(RedactingFilter())
