"""Integration tests for /api/reviews endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Category,
    Finding,
    FindingStatus,
    Review,
    ReviewFile,
    ReviewStatus,
    Severity,
    Source,
)


def test_create_review_success(client: TestClient, db_session: Session):
    """Verify POST /api/reviews parses URL and persists queued review."""
    with patch("app.api.reviews.run_review", new_callable=AsyncMock) as mock_runner:
        # Test with /files suffix to ensure canonical normalization
        payload = {"pr_url": "https://github.com/octocat/Hello-World/pull/42/files"}
        response = client.post("/api/reviews", json=payload)

        assert response.status_code == 202
        data = response.json()
        assert "id" in data
        assert data["status"] == "queued"

        review_id = data["id"]
        mock_runner.assert_called_once()
        persisted = db_session.scalar(select(Review).where(Review.id == review_id))
        assert persisted is not None
        assert (
            persisted.pr_url == "https://github.com/octocat/Hello-World/pull/42"
        )  # canonical
        assert persisted.owner == "octocat"
        assert persisted.repo == "Hello-World"
        assert persisted.pr_number == 42
        assert persisted.status == ReviewStatus.QUEUED
        assert persisted.current_stage == "queued"


def test_create_review_invalid_url_creates_no_record(
    client: TestClient, db_session: Session
):
    """Verify invalid PR URLs return 422 and create zero database records."""
    invalid_urls = [
        "   ",
        "http://github.com/octocat/Hello-World/pull/42",
        "https://evil.com/octocat/Hello-World/pull/42",
        "https://github.com/octocat/Hello-World/pull/0",
    ]

    initial_count = len(db_session.scalars(select(Review)).all())

    for bad_url in invalid_urls:
        response = client.post("/api/reviews", json={"pr_url": bad_url})
        assert response.status_code == 422
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] in {"invalid_pr_url", "validation_error"}

    # Assert no records were added to the database
    final_count = len(db_session.scalars(select(Review)).all())
    assert final_count == initial_count


def test_list_reviews_pagination(client: TestClient, db_session: Session):
    """Verify GET /api/reviews returns paginated list ordered newest first."""
    # Seed 5 reviews
    created_ids = []
    for i in range(5):
        rid = str(uuid.uuid4())
        created_ids.append(rid)
        review = Review(
            id=rid,
            pr_url=f"https://github.com/org/repo/pull/{i + 1}",
            owner="org",
            repo="repo",
            pr_number=i + 1,
            status=ReviewStatus.QUEUED,
            current_stage="queued",
        )
        db_session.add(review)
    db_session.commit()

    # Request page 1 with page_size=2
    resp_page_1 = client.get("/api/reviews?page=1&page_size=2")
    assert resp_page_1.status_code == 200
    data_1 = resp_page_1.json()
    assert data_1["total"] >= 5
    assert data_1["page"] == 1
    assert data_1["page_size"] == 2
    assert len(data_1["items"]) == 2

    # Request page 2 with page_size=2
    resp_page_2 = client.get("/api/reviews?page=2&page_size=2")
    assert resp_page_2.status_code == 200
    data_2 = resp_page_2.json()
    assert len(data_2["items"]) == 2
    # Ensure distinct items across pages
    page_1_ids = [item["id"] for item in data_1["items"]]
    page_2_ids = [item["id"] for item in data_2["items"]]
    assert not set(page_1_ids).intersection(set(page_2_ids))


def test_get_nonexistent_review_404_envelope(client: TestClient):
    """Verify retrieving nonexistent review returns 404 with standard envelope."""
    missing_id = str(uuid.uuid4())
    response = client.get(f"/api/reviews/{missing_id}")

    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "review_not_found"
    assert missing_id in data["error"]["message"]
    assert "request_id" in data["error"]
    assert data["error"]["request_id"] != ""


def test_delete_review_and_cascade(client: TestClient, db_session: Session):
    """Verify review deletion cascades to associated ReviewFile and Finding rows."""
    rid = str(uuid.uuid4())
    review = Review(
        id=rid,
        pr_url="https://github.com/org/repo/pull/99",
        owner="org",
        repo="repo",
        pr_number=99,
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
    )
    db_session.add(review)
    db_session.flush()

    file_row = ReviewFile(
        review_id=rid,
        path="src/main.py",
        status="modified",
        additions=10,
        deletions=2,
    )
    finding_row = Finding(
        review_id=rid,
        file_path="src/main.py",
        line_start=15,
        line_end=16,
        title="SQL Injection Risk",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        source=Source.STATIC,
        tool_name="bandit",
        rule_id="B608",
        evidence="query = f'SELECT * FROM users WHERE id = {uid}'",
        explanation="Raw string interpolation in SQL query.",
        confidence=0.95,
        fingerprint="fp-test-1234567890",
        status=FindingStatus.OPEN,
    )
    db_session.add_all([file_row, finding_row])
    db_session.commit()

    # Delete the review via API
    delete_resp = client.delete(f"/api/reviews/{rid}")
    assert delete_resp.status_code == 204

    # Verify review and all cascaded children are completely removed
    assert db_session.scalar(select(Review).where(Review.id == rid)) is None
    assert (
        db_session.scalar(select(ReviewFile).where(ReviewFile.review_id == rid)) is None
    )
    assert db_session.scalar(select(Finding).where(Finding.review_id == rid)) is None
