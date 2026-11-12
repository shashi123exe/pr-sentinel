"""Unit tests for FastAPI lifespan startup recovery."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Review, ReviewStatus
from app.main import app, lifespan


@pytest.mark.asyncio
async def test_startup_recovers_running_reviews_to_failed(db_session: Session):
    """Verify lifespan startup recovers running reviews to failed."""
    running_id = str(uuid.uuid4())
    queued_id = str(uuid.uuid4())

    running_review = Review(
        id=running_id,
        pr_url="https://github.com/org/repo/pull/1",
        status=ReviewStatus.RUNNING,
        current_stage="ai_analysis",
    )
    queued_review = Review(
        id=queued_id,
        pr_url="https://github.com/org/repo/pull/2",
        status=ReviewStatus.QUEUED,
        current_stage="queued",
    )
    db_session.add_all([running_review, queued_review])
    db_session.commit()

    # Invoke lifespan startup passing the configured test app
    async with lifespan(app):
        pass

    db_session.expire_all()
    recovered_running = db_session.scalar(select(Review).where(Review.id == running_id))
    assert recovered_running is not None
    assert recovered_running.status == ReviewStatus.FAILED
    assert recovered_running.error_code == "server_restart"
    assert "server restart" in (recovered_running.error_message or "")

    # Queued review must remain untouched
    intact_queued = db_session.scalar(select(Review).where(Review.id == queued_id))
    assert intact_queued is not None
    assert intact_queued.status == ReviewStatus.QUEUED
    assert intact_queued.error_code is None
