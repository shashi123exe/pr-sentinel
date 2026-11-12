"""Finding management and triage API endpoints."""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Finding
from app.db.session import get_db
from app.errors import AppError
from app.schemas.review import FindingOut, FindingStatusUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/findings", tags=["findings"])


@router.get("/{id}", response_model=FindingOut, status_code=status.HTTP_200_OK)
def get_finding(
    id: int,
    db: Annotated[Session, Depends(get_db)],
) -> FindingOut:
    """Retrieve details of a single finding by primary key ID."""
    finding = db.scalar(select(Finding).where(Finding.id == id))
    if finding is None:
        raise AppError(
            code="finding_not_found",
            message=f"Finding '{id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return FindingOut.model_validate(finding)


@router.patch("/{id}", response_model=FindingOut, status_code=status.HTTP_200_OK)
def update_finding_status(
    id: int,
    update_in: FindingStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> FindingOut:
    """Update user triage status for a finding (open, approved, dismissed)."""
    finding = db.scalar(select(Finding).where(Finding.id == id))
    if finding is None:
        raise AppError(
            code="finding_not_found",
            message=f"Finding '{id}' not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    finding.status = update_in.status
    finding.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(finding)

    logger.info(
        "Updated finding [%d] triage status to '%s'", id, update_in.status.value
    )
    return FindingOut.model_validate(finding)
