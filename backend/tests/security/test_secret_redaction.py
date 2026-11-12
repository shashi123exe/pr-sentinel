"""Security tests for secret redaction, log scrubbing, and error safety."""

import io
import logging

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.errors import AppError, app_error_handler, unhandled_exception_handler
from app.logging_config import (
    RedactingFilter,
    RedactingFormatter,
    redact_sensitive_text,
)


class TestSecretRedactionAndLoggingSafety:
    """Rigorous security testing verifying secrets are never logged or leaked."""

    def test_direct_string_redaction_patterns(self):
        """Verify redact_sensitive_text neutralizes all known token formats."""
        test_cases = [
            # GitHub classic PAT
            (
                "Failed with token ghp_111122223333444455556666777788889999",
                "Failed with token [REDACTED_GITHUB_TOKEN]",
            ),
            # GitHub fine-grained PAT
            (
                "Connecting via github_pat_11AABBCCDDEEFF00112233445566778899",
                "Connecting via [REDACTED_GITHUB_TOKEN]",
            ),
            # Google Gemini API key
            (
                "Gemini key AIzaSyD12345678901234567890123456789012 initialized",
                "Gemini key [REDACTED_GEMINI_KEY] initialized",
            ),
            # Bearer token in text
            (
                "Using Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz",
                "Using Bearer [REDACTED_TOKEN]",
            ),
            # Generic Authorization header
            (
                "Authorization: Basic dXNlcjpwYXNzd29yZA==",
                "Authorization: [REDACTED_AUTH]",
            ),
            (
                "authorization=token_abc1234567890",
                "authorization=[REDACTED_AUTH]",
            ),
        ]

        for raw, expected in test_cases:
            assert redact_sensitive_text(raw) == expected

    def test_logging_filter_scrubs_records(self):
        """Verify RedactingFilter redacts msg, args tuple, and args dict."""
        filt = RedactingFilter()

        # 1. Secret in msg
        rec1 = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Call with ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            args=(),
            exc_info=None,
        )
        filt.filter(rec1)
        assert "[REDACTED_GITHUB_TOKEN]" in rec1.msg
        assert "ghp_" not in rec1.msg

        # 2. Secret in args tuple
        rec2 = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=20,
            msg="User token: %s",
            args=("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",),
            exc_info=None,
        )
        filt.filter(rec2)
        assert rec2.args[0] == "[REDACTED_GITHUB_TOKEN]"

        # 3. Secret in args dict
        rec3 = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=30,
            msg="User details: %(token)s",
            args=({"token": "AIzaSyD12345678901234567890123456789012"},),
            exc_info=None,
        )
        filt.filter(rec3)
        assert rec3.args["token"] == "[REDACTED_GEMINI_KEY]"

    def test_formatter_redacts_exception_tracebacks(self):
        """Verify RedactingFormatter redacts secrets embedded in tracebacks."""
        formatter = RedactingFormatter(fmt="%(message)s")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(formatter)
        logger = logging.getLogger("security_traceback_test")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        try:
            # Raise exception containing token in message
            secret_key = "AIzaSyD12345678901234567890123456789012"
            raise RuntimeError(f"Connection failed for key: {secret_key}")
        except RuntimeError:
            logger.exception("An error occurred during request")

        output = stream.getvalue()
        assert secret_key not in output
        assert "[REDACTED_GEMINI_KEY]" in output

    @pytest.mark.asyncio
    async def test_fastapi_error_handlers_do_not_leak_internals(self):
        """Verify error handlers produce clean JSON envelopes without tracebacks."""
        # Mock request
        scope = {"type": "http", "method": "GET", "path": "/api/reviews"}
        req = Request(scope)

        # 1. Custom AppError
        app_err = AppError(
            code="test_error",
            message="Clean user message",
            status_code=400,
        )
        res1: JSONResponse = await app_error_handler(req, app_err)
        assert res1.status_code == 400
        # Parse body
        body1 = res1.body.decode("utf-8")
        assert "traceback" not in body1
        assert "Clean user message" in body1

        # 2. Unexpected generic Exception
        gen_err = ValueError("Internal secret ghp_AABBCCDDEEFFGGHHIIJJKKLLMMNN")
        res2: JSONResponse = await unhandled_exception_handler(req, gen_err)
        assert res2.status_code == 500
        body2 = res2.body.decode("utf-8")
        # Internal secret from ValueError must not be leaked in response body
        assert "ghp_" not in body2
        assert "internal_server_error" in body2
