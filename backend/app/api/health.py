"""Health check API endpoints."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.errors import AppError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
def check_health(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """Perform health and database connectivity check."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        raise AppError(
            code="database_unavailable",
            message="Database connectivity check failed.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from exc

    return {"status": "ok", "database": "ok"}
