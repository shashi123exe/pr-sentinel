"""Integration tests for Phase 6 review endpoints (/findings, /files, progress)."""

import uuid

from fastapi.testclient import TestClient
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


def test_get_review_progress_and_warnings(client: TestClient, db_session: Session):
    """Verify GET /api/reviews/{id} returns progress_percent and warnings."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/owner/repo/pull/1",
        owner="owner",
        repo="repo",
        pr_number=1,
        status=ReviewStatus.RUNNING,
        current_stage="ai_review",
        warnings=["AI batch warning: minor JSON repair"],
    )
    db_session.add(review)
    db_session.commit()

    resp = client.get(f"/api/reviews/{review_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == review_id
    assert data["status"] == "running"
    assert data["current_stage"] == "ai_review"
    assert data["progress_percent"] == 80  # 80% for ai_review
    assert data["warnings"] == ["AI batch warning: minor JSON repair"]


def test_list_review_findings_endpoint(client: TestClient, db_session: Session):
    """Verify GET /api/reviews/{id}/findings returns paginated findings."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/owner/repo/pull/1",
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
    )
    db_session.add(review)

    # Add 2 findings
    f1 = Finding(
        review_id=review_id,
        file_path="app/auth.py",
        line_start=10,
        line_end=12,
        title="Hardcoded JWT Secret",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        source=Source.STATIC,
        tool_name="bandit",
        rule_id="B105",
        evidence="secret = 'abc'",
        explanation="Hardcoded credential",
        confidence=1.0,
        fingerprint="fp1" + "0" * 61,
        status=FindingStatus.OPEN,
    )
    f2 = Finding(
        review_id=review_id,
        file_path="app/auth.py",
        line_start=20,
        line_end=20,
        title="SQL injection risk",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        source=Source.AI,
        tool_name="gemini",
        rule_id="ai_review",
        evidence="db.execute(query)",
        explanation="String formatting in query",
        suggested_fix="Use parameterized queries",
        confidence=0.95,
        fingerprint="fp2" + "0" * 61,
        status=FindingStatus.OPEN,
    )
    db_session.add_all([f1, f2])
    db_session.commit()

    # Query findings
    resp = client.get(f"/api/reviews/{review_id}/findings?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    # In Phase 7, default sort is severity descending (Critical before High)
    assert data["items"][0]["title"] == "SQL injection risk"
    assert data["items"][0]["source"] == "ai"
    assert data["items"][0]["suggested_fix"] == "Use parameterized queries"
    assert data["items"][1]["title"] == "Hardcoded JWT Secret"
    assert data["items"][1]["source"] == "static"


def test_list_review_files_endpoint(client: TestClient, db_session: Session):
    """Verify GET /api/reviews/{id}/files returns paginated files."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/owner/repo/pull/1",
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
    )
    db_session.add(review)

    rf1 = ReviewFile(
        review_id=review_id,
        path="app/main.py",
        status="modified",
        additions=5,
        deletions=2,
        language="python",
        patch="@@ -1 +1 @@",
    )
    rf2 = ReviewFile(
        review_id=review_id,
        path="package-lock.json",
        status="modified",
        additions=500,
        deletions=200,
        skipped_reason="lockfile",
    )
    db_session.add_all([rf1, rf2])
    db_session.commit()

    resp = client.get(f"/api/reviews/{review_id}/files?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["path"] == "app/main.py"
    assert data["items"][0]["skipped_reason"] is None
    assert data["items"][1]["path"] == "package-lock.json"
    assert data["items"][1]["skipped_reason"] == "lockfile"


def test_endpoints_return_404_for_nonexistent_review(client: TestClient):
    """Verify /findings and /files return 404 when review does not exist."""
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp_findings = client.get(f"/api/reviews/{fake_id}/findings")
    assert resp_findings.status_code == 404
    assert resp_findings.json()["error"]["code"] == "review_not_found"

    resp_files = client.get(f"/api/reviews/{fake_id}/files")
    assert resp_files.status_code == 404
    assert resp_files.json()["error"]["code"] == "review_not_found"


def test_repeated_polling_progress_across_stages(
    client: TestClient, db_session: Session
):
    """Verify repeated polling of GET /api/reviews/{id} reflects progress."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/owner/repo/pull/1",
        owner="owner",
        repo="repo",
        pr_number=1,
        status=ReviewStatus.QUEUED,
        current_stage="queued",
    )
    db_session.add(review)
    db_session.commit()

    # 1. Queued: 0%
    resp = client.get(f"/api/reviews/{review_id}")
    assert resp.status_code == 200
    assert resp.json()["progress_percent"] == 0
    assert resp.json()["status"] == "queued"

    # 2. Stage transitions while running
    stages = [
        ("fetch_pr", 10),
        ("process_diff", 25),
        ("static_analysis", 45),
        ("retrieve_context", 60),
        ("ai_review", 80),
        ("validate_findings", 90),
        ("persist_results", 95),
    ]
    for stage, expected_pct in stages:
        review.status = ReviewStatus.RUNNING
        review.current_stage = stage
        db_session.commit()
        resp = client.get(f"/api/reviews/{review_id}")
        assert resp.json()["progress_percent"] == expected_pct
        assert resp.json()["status"] == "running"

    # 3. Completed: 100%
    review.status = ReviewStatus.COMPLETED
    review.current_stage = "completed"
    db_session.commit()
    resp = client.get(f"/api/reviews/{review_id}")
    assert resp.json()["progress_percent"] == 100
    assert resp.json()["status"] == "completed"

    # 4. Failed: retains stage % and details
    review.status = ReviewStatus.FAILED
    review.current_stage = "ai_review"
    review.error_code = "ai_unavailable"
    review.error_message = "Gemini API unavailable"
    db_session.commit()
    resp = client.get(f"/api/reviews/{review_id}")
    assert resp.json()["progress_percent"] == 80
    assert resp.json()["status"] == "failed"
    assert resp.json()["error_code"] == "ai_unavailable"
    assert resp.json()["error_message"] == "Gemini API unavailable"


def test_invalid_pagination_parameters_rejected(
    client: TestClient, db_session: Session
):
    """Verify invalid pagination parameters (page < 1 or page_size > 100) return 422."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/owner/repo/pull/1",
        status=ReviewStatus.COMPLETED,
        current_stage="completed",
    )
    db_session.add(review)
    db_session.commit()

    # page < 1
    resp1 = client.get(f"/api/reviews/{review_id}/findings?page=0")
    assert resp1.status_code == 422

    # page_size > 100
    resp2 = client.get(f"/api/reviews/{review_id}/findings?page_size=101")
    assert resp2.status_code == 422

    # files endpoint invalid page
    resp3 = client.get(f"/api/reviews/{review_id}/files?page=-1")
    assert resp3.status_code == 422
