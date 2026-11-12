"""Shared access token verification dependency for PR Sentinel API endpoints."""

import secrets
from typing import Annotated

from fastapi import Depends, Request, status

from app.config import Settings, get_settings
from app.errors import AppError


def verify_access_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Verify shared access token if APP_ACCESS_TOKEN is configured.

    When APP_ACCESS_TOKEN is not configured or empty, access gating is disabled,
    preserving local development and unauthenticated usage.

    When configured, incoming requests must include a valid 'X-Access-Token'
    header (or fallback 'Authorization: Bearer <token>'). Comparison is performed
    in constant time using secrets.compare_digest.
    """
    configured_token = settings.APP_ACCESS_TOKEN
    if not configured_token:
        return

    expected_secret = configured_token.get_secret_value().strip()
    if not expected_secret:
        return

    # 1. Primary custom header contract: X-Access-Token
    provided_token = request.headers.get("X-Access-Token")

    # 2. Standard HTTP Authorization header fallback: Bearer <token>
    if not provided_token:
        auth_header = request.headers.get("Authorization")
        if auth_header:
            parts = auth_header.split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                provided_token = parts[1].strip()

    if not provided_token:
        raise AppError(
            code="unauthorized",
            message=(
                "Access token required. Please provide a valid 'X-Access-Token' header."
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not secrets.compare_digest(
        provided_token.strip().encode("utf-8"), expected_secret.encode("utf-8")
    ):
        raise AppError(
            code="unauthorized",
            message="Invalid access token.",
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
