"""Application settings configuration API endpoints."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AppSettings
from app.db.session import get_db, seed_default_settings
from app.errors import AppError
from app.schemas.settings import SettingsOut, SettingsUpdate

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


def get_or_create_settings(db: Session) -> AppSettings:
    """Retrieve the singleton settings record, seeding it if missing."""
    settings_record = db.scalar(select(AppSettings).where(AppSettings.id == 1))
    if settings_record is None:
        seed_default_settings(db)
        settings_record = db.scalar(select(AppSettings).where(AppSettings.id == 1))
        if settings_record is None:
            raise AppError(
                code="settings_unavailable",
                message="Application settings record could not be loaded.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
    return settings_record


@router.get("", response_model=SettingsOut, status_code=status.HTTP_200_OK)
def get_app_settings(
    db: Annotated[Session, Depends(get_db)],
) -> SettingsOut:
    """Retrieve global analysis settings."""
    settings_record = get_or_create_settings(db)
    return SettingsOut.model_validate(settings_record)


@router.put("", response_model=SettingsOut, status_code=status.HTTP_200_OK)
def update_app_settings(
    update_data: SettingsUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> SettingsOut:
    """Update global analysis settings with validated parameters."""
    settings_record = get_or_create_settings(db)

    # Apply only provided updates
    payload = update_data.model_dump(exclude_unset=True)
    if "max_files" in payload and payload["max_files"] is not None:
        settings_record.max_files = payload["max_files"]
    if "max_diff_bytes" in payload and payload["max_diff_bytes"] is not None:
        settings_record.max_diff_bytes = payload["max_diff_bytes"]
    if "min_confidence" in payload and payload["min_confidence"] is not None:
        settings_record.min_confidence = payload["min_confidence"]
    if "enabled_categories" in payload and payload["enabled_categories"] is not None:
        settings_record.enabled_categories = [
            cat.value if hasattr(cat, "value") else str(cat)
            for cat in payload["enabled_categories"]
        ]
    if "gemini_model" in payload and payload["gemini_model"] is not None:
        settings_record.gemini_model = payload["gemini_model"].strip()

    db.add(settings_record)
    db.flush()
    db.refresh(settings_record)

    logger.info("Updated AppSettings (id=1).")
    return SettingsOut.model_validate(settings_record)
