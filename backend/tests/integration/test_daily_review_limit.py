"""Integration and concurrency tests for atomic rolling 24-hour daily review limit."""

from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import AppSettings, Category, Review, ReviewStatus
from app.db.session import build_engine, get_db, init_db
from app.main import create_app
from app.rate_limit import limiter


def create_test_client(
    tmp_path: Path,
    name: str = "test",
    max_reviews: int = 5,
) -> tuple[TestClient, sessionmaker]:
    """Helper creating a test application with custom daily review limit."""
    db_file = tmp_path / f"limit_{name}.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    test_settings = Settings(
        DATABASE_URL=db_url,
        APP_ACCESS_TOKEN=None,
        MAX_REVIEWS_PER_DAY=max_reviews,
        LOG_LEVEL="WARNING",
    )

    test_engine = build_engine(db_url)
    init_db(engine_override=test_engine)
    test_session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    with test_session_factory() as db:
        if db.query(AppSettings).filter_by(id=1).first() is None:
            db.add(
                AppSettings(
                    id=1,
                    max_files=50,
                    max_diff_bytes=500000,
                    min_confidence=0.5,
                    enabled_categories=[c.value for c in Category],
                    gemini_model="",
                )
            )
            db.commit()

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    application = create_app(settings_override=test_settings)
    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: test_settings
    application.state.engine = test_engine
    application.state.session_factory = test_session_factory

    client = TestClient(application)
    return client, test_session_factory


@pytest.fixture(autouse=True)
def restore_limiter_state() -> Generator[None, None, None]:
    """Ensure limiter is clean and enabled before and after every test."""
    limiter.enabled = True
    limiter.reset()
    try:
        yield
    finally:
        limiter.enabled = True
        limiter.reset()


class TestDailyReviewLimit:
    """Validate rolling 24-hour review limit enforcement and concurrency safety."""

    @patch("app.api.reviews.run_review")
    def test_daily_review_limit_enforced(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """When MAX_REVIEWS_PER_DAY is reached, subsequent submissions receive 429."""
        client, session_factory = create_test_client(
            tmp_path, name="enforced", max_reviews=2
        )

        # First review submission
        res1 = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/1"},
        )
        assert res1.status_code == 202
        assert res1.json()["status"] == "queued"

        # Second review submission (reaches limit)
        res2 = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/2"},
        )
        assert res2.status_code == 202

        # Third review submission (exceeds limit)
        res3 = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/3"},
        )
        assert res3.status_code == 429
        body = res3.json()
        assert body["error"]["code"] == "daily_limit_reached"
        assert "Daily review limit reached" in body["error"]["message"]

    @patch("app.api.reviews.run_review")
    def test_reviews_older_than_24h_do_not_count(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Reviews created more than 24 hours ago are outside the rolling window."""
        client, session_factory = create_test_client(
            tmp_path, name="old", max_reviews=1
        )

        # Insert a 25-hour-old review directly into the DB
        with session_factory() as db:
            old_review = Review(
                id="old-review-1",
                pr_url="https://github.com/pallets/flask/pull/10",
                owner="pallets",
                repo="flask",
                pr_number=10,
                status=ReviewStatus.COMPLETED,
                current_stage="completed",
                warnings=[],
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
            db.add(old_review)
            db.commit()

        # With limit=1, a new review should succeed because the existing
        # one is outside the 24h window
        response = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/11"},
        )
        assert response.status_code == 202

    @patch("app.api.reviews.run_review")
    def test_failed_reviews_consume_quota(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Reviews that failed still consume quota to protect LLM spending."""
        client, session_factory = create_test_client(
            tmp_path, name="failed", max_reviews=1
        )

        # Insert a recently failed review into the DB
        with session_factory() as db:
            failed_review = Review(
                id="failed-review-1",
                pr_url="https://github.com/pallets/flask/pull/20",
                owner="pallets",
                repo="flask",
                pr_number=20,
                status=ReviewStatus.FAILED,
                current_stage="failed",
                error_code="rate_limited",
                error_message="GitHub rate limited",
                warnings=[],
                created_at=datetime.now(UTC) - timedelta(minutes=10),
            )
            db.add(failed_review)
            db.commit()

        # With limit=1, new review must be rejected because failed reviews consume quota
        response = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/21"},
        )
        assert response.status_code == 429
        body = response.json()
        assert body["error"]["code"] == "daily_limit_reached"

    @patch("app.api.reviews.run_review")
    def test_rolling_24h_boundary_23h59m_counts(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """A review created 23h 59m ago is within the rolling 24h window."""
        client, session_factory = create_test_client(
            tmp_path, name="boundary_inside", max_reviews=1
        )

        with session_factory() as db:
            recent_review = Review(
                id="recent-review-1",
                pr_url="https://github.com/pallets/flask/pull/30",
                owner="pallets",
                repo="flask",
                pr_number=30,
                status=ReviewStatus.COMPLETED,
                current_stage="completed",
                warnings=[],
                created_at=datetime.now(UTC) - timedelta(hours=23, minutes=59),
            )
            db.add(recent_review)
            db.commit()

        # With limit=1, review is inside the 24h window; next must be rejected
        response = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/31"},
        )
        assert response.status_code == 429
        body = response.json()
        assert body["error"]["code"] == "daily_limit_reached"

    @patch("app.api.reviews.run_review")
    def test_rolling_24h_boundary_24h01m_does_not_count(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """A review created 24h 01m ago is outside the rolling 24h window."""
        client, session_factory = create_test_client(
            tmp_path, name="boundary_outside", max_reviews=1
        )

        with session_factory() as db:
            old_review = Review(
                id="old-review-2",
                pr_url="https://github.com/pallets/flask/pull/40",
                owner="pallets",
                repo="flask",
                pr_number=40,
                status=ReviewStatus.COMPLETED,
                current_stage="completed",
                warnings=[],
                created_at=datetime.now(UTC) - timedelta(hours=24, minutes=1),
            )
            db.add(old_review)
            db.commit()

        # With limit=1, the review is outside the 24h window, so new submission succeeds
        response = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/41"},
        )
        assert response.status_code == 202

    @patch("app.api.reviews.run_review")
    def test_limit_zero_disables_quota(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """When MAX_REVIEWS_PER_DAY=0, the limit is entirely disabled."""
        client, _ = create_test_client(tmp_path, name="zero", max_reviews=0)

        for i in range(4):
            res = client.post(
                "/api/reviews",
                json={"pr_url": f"https://github.com/pallets/flask/pull/{100 + i}"},
            )
            assert res.status_code == 202

    @patch("app.api.reviews.run_review")
    def test_concurrent_review_submissions_strictly_serialized(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """Multiple simultaneous requests cannot race past the limit.

        Spawns 8 concurrent threads competing for 2 available review slots.
        Verifies that EXACTLY 2 requests succeed (202) and 6 are rejected (429),
        with no overbooking in the database.
        """
        limit = 2
        total_requests = 8
        client, session_factory = create_test_client(
            tmp_path, name="concurrent", max_reviews=limit
        )

        def submit(idx: int) -> int:
            res = client.post(
                "/api/reviews",
                json={"pr_url": f"https://github.com/pallets/flask/pull/{200 + idx}"},
            )
            return res.status_code

        try:
            limiter.enabled = False
            with ThreadPoolExecutor(max_workers=total_requests) as executor:
                futures = [executor.submit(submit, i) for i in range(total_requests)]
                status_codes = [f.result() for f in futures]
        finally:
            limiter.enabled = True
            limiter.reset()

        accepted = status_codes.count(202)
        rate_limited = status_codes.count(429)

        assert accepted == limit, (
            f"Expected exactly {limit} accepted reviews, got {accepted}"
        )
        assert rate_limited == total_requests - limit, (
            f"Expected {total_requests - limit} 429 rejections, got {rate_limited}"
        )

        # Verify DB row count matches limit
        with session_factory() as db:
            count = len(db.scalars(select(Review)).all())
            assert count == limit

    @patch("app.api.reviews.run_review")
    def test_daily_limit_persists_across_application_restart(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """When database persists, daily review quota count survives app restart."""
        limit = 1

        # Process Generation 1: App creates review and reaches quota
        client_gen1, _ = create_test_client(
            tmp_path, name="persists", max_reviews=limit
        )
        res1 = client_gen1.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/50"},
        )
        assert res1.status_code == 202

        # Process Generation 2: New application instance boots pointing to same DB
        client_gen2, _ = create_test_client(
            tmp_path, name="persists", max_reviews=limit
        )

        # Quota remains exhausted immediately after restart
        res2 = client_gen2.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/pallets/flask/pull/51"},
        )
        assert res2.status_code == 429
        body = res2.json()
        assert body["error"]["code"] == "daily_limit_reached"
