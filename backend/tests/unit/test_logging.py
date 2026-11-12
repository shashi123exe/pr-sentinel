"""Unit tests for logging redaction filter and request ID formatting."""

import logging

from app.logging_config import RedactingFilter, redact_sensitive_text, request_id_ctx


def test_redact_github_tokens():
    """Verify GitHub classic and fine-grained personal access tokens are scrubbed."""
    classic_token = "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB"
    fine_grained_token = (
        "github_pat_11AAAAAAA0123456789abcdefghijklmnopqrstuvwxyz_0123456789"
    )

    msg = f"Fetching PR using token {classic_token} and auth {fine_grained_token}"
    redacted = redact_sensitive_text(msg)

    assert classic_token not in redacted
    assert fine_grained_token not in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted


def test_redact_gemini_api_key():
    """Verify Google Gemini AIza API keys are scrubbed."""
    api_key = "AIzaSyD-1234567890abcdefghijklmnopqrstuv"
    msg = f"Connecting to Gemini API with key: {api_key}"
    redacted = redact_sensitive_text(msg)

    assert api_key not in redacted
    assert "[REDACTED_GEMINI_KEY]" in redacted


def test_redact_authorization_bearer_headers():
    """Verify Authorization and Bearer headers are scrubbed."""
    raw = "Headers: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz"
    redacted = redact_sensitive_text(raw)

    assert "Bearer [REDACTED_TOKEN]" in redacted or "[REDACTED_AUTH]" in redacted
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in redacted


def test_redacting_filter_on_log_record():
    """Verify RedactingFilter scrubs log records and attaches request ID."""
    token = request_id_ctx.set("req-test-uuid-1234")
    try:
        log_filter = RedactingFilter()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname=__file__,
            lineno=42,
            msg="User token is %s and key is %s",
            args=(
                "ghp_1234567890abcdefghijklmnopqrstuvwxyzAB",
                "AIzaSyD-1234567890abcdefghijklmnopqrstuv",
            ),
            exc_info=None,
        )

        assert log_filter.filter(record) is True
        assert record.request_id == "req-test-uuid-1234"
        assert isinstance(record.args, tuple)
        assert "ghp_" not in record.args[0]
        assert "[REDACTED_GITHUB_TOKEN]" in record.args[0]
        assert "AIza" not in record.args[1]
        assert "[REDACTED_GEMINI_KEY]" in record.args[1]
    finally:
        request_id_ctx.reset(token)
