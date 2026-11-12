"""Comprehensive test suite for Phase 9 Failure Drills A through E.

Verifies:
- Drill A: Invalid PR URL rejection (HTTP 422, zero DB records created).
- Drill B: Nonexistent PR URL safe failure (transitions to failed, not stuck).
- Drill C: Missing Gemini API key handling (respects static fallback policy).
- Drill D: Server restart / ungraceful interruption recovery via lifespan.
- Drill E: API rate limit (5 requests/min accepted, 6th rejected with 429).
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.runner import StaticAnalysisRunner
from app.db.models import Review, ReviewStatus
from app.errors import AppError
from app.graph.builder import build_graph
from app.graph.nodes import GraphDependencies
from app.main import app, lifespan
from app.rate_limit import limiter
from app.schemas.analysis import StaticAnalysisResult
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot


# ==============================================================================
# DRILL A: Invalid PR URL
# ==============================================================================
def test_drill_a_invalid_pr_url_rejected_with_422(
    client: TestClient, db_session: Session
):
    """Drill A: Verify invalid PR URL returns 422 and creates no review."""
    initial_count = len(db_session.scalars(select(Review)).all())

    invalid_payloads = [
        {"pr_url": ""},
        {"pr_url": "not-a-valid-url"},
        {"pr_url": "https://gitlab.com/owner/repo/merge_requests/1"},
        {"pr_url": "https://github.com/owner/repo/issues/42"},
    ]

    for payload in invalid_payloads:
        resp = client.post("/api/reviews", json=payload)
        assert resp.status_code == 422, (
            f"Expected 422 for {payload}, got {resp.status_code}"
        )
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] in ("validation_error", "invalid_pr_url")
        assert "message" in data["error"]

    # Verify zero records created
    final_count = len(db_session.scalars(select(Review)).all())
    assert final_count == initial_count


# ==============================================================================
# DRILL B: Nonexistent Public PR
# ==============================================================================
@pytest.mark.asyncio
async def test_drill_b_nonexistent_pr_transitions_safely_to_failed(
    client: TestClient, db_session: Session
):
    """Drill B: Verify nonexistent PR transitions safely to failed status."""
    nonexistent_url = (
        "https://github.com/google/nonexistent-sentinel-test-repo/pull/999"
    )

    # Step 1: Submit review via API
    resp = client.post("/api/reviews", json={"pr_url": nonexistent_url})
    assert resp.status_code == 202
    review_id = resp.json()["id"]

    # Step 2: Configure mocked GitHubClient raising 404
    mock_github = AsyncMock()
    mock_github.fetch_pr_snapshot.side_effect = AppError(
        code="github_not_found",
        message="Pull request or repository not found.",
        status_code=404,
    )

    deps = GraphDependencies(
        session_factory=lambda: db_session,
        github_client=mock_github,
    )
    graph = build_graph(deps).compile()

    initial_state = {
        "review_id": review_id,
        "pr_url": nonexistent_url,
        "parsed_pr": {
            "owner": "google",
            "repo": "nonexistent-sentinel-test-repo",
            "number": 999,
            "canonical_url": nonexistent_url,
        },
        "errors": [],
        "warnings": [],
    }

    # Execute graph
    final_state = await graph.ainvoke(initial_state)

    # Step 3: Verify safe failure
    assert final_state.get("failed") is True
    persisted = db_session.scalar(select(Review).where(Review.id == review_id))
    assert persisted is not None
    assert persisted.status == ReviewStatus.FAILED
    assert persisted.error_code == "github_not_found"
    assert "not found" in persisted.error_message.lower()


# ==============================================================================
# DRILL C: Missing Gemini Configuration Fallback
# ==============================================================================
@pytest.mark.asyncio
async def test_drill_c_missing_gemini_config_fallback_policy(db_session: Session):
    """Drill C: Verify missing Gemini respects static fallback policy."""
    review_id = str(uuid.uuid4())
    review = Review(
        id=review_id,
        pr_url="https://github.com/org/repo/pull/1",
        owner="org",
        repo="repo",
        pr_number=1,
        status=ReviewStatus.QUEUED,
        current_stage="queued",
    )
    db_session.add(review)
    db_session.commit()

    # Mock snapshot with a simple file
    mock_snapshot = PRSnapshot(
        metadata=PRMetadata(
            title="Update dependencies",
            author="dev",
            base_sha="abc1234567890abcdef1234567890abcdef12",
            head_sha="def567890abcdef1234567890abcdef12345",
            base_ref="main",
            head_ref="dep-update",
            state="open",
            html_url="https://github.com/org/repo/pull/1",
            changed_files=1,
            additions=1,
            deletions=0,
        ),
        files=[
            ChangedFile(
                path="src/main.py",
                status="modified",
                additions=1,
                deletions=0,
                patch="@@ -1,1 +1,2 @@\n context\n+new line",
            )
        ],
        truncated=False,
    )

    mock_github = AsyncMock()
    mock_github.fetch_pr_snapshot.return_value = mock_snapshot
    mock_github.get_file_content.return_value = "context\nnew line\n"

    # Gemini client raising AI unavailable error
    mock_gemini = AsyncMock()
    mock_gemini.generate_review.side_effect = AppError(
        code="ai_unavailable",
        message="GEMINI_API_KEY is not configured.",
        status_code=503,
    )

    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[], tool_errors=[], tools_run=[]
    )

    deps = GraphDependencies(
        session_factory=lambda: db_session,
        github_client=mock_github,
        gemini_client=mock_gemini,
        analysis_runner=mock_runner,
    )
    graph = build_graph(deps).compile()

    initial_state = {
        "review_id": review_id,
        "pr_url": "https://github.com/org/repo/pull/1",
        "parsed_pr": {
            "owner": "org",
            "repo": "repo",
            "number": 1,
            "canonical_url": "https://github.com/org/repo/pull/1",
        },
        "errors": [],
        "warnings": [],
    }

    await graph.ainvoke(initial_state)

    persisted = db_session.scalar(select(Review).where(Review.id == review_id))
    assert persisted is not None
    # Review must reach a terminal state, never remain RUNNING or QUEUED
    assert persisted.status in (ReviewStatus.COMPLETED, ReviewStatus.FAILED)


# ==============================================================================
# DRILL D: Server Restart / Lifespan Recovery
# ==============================================================================
@pytest.mark.asyncio
async def test_drill_d_server_restart_recovers_running_reviews(db_session: Session):
    """Drill D: Verify application lifespan marks running reviews as failed."""
    stale_id = str(uuid.uuid4())
    stale_review = Review(
        id=stale_id,
        pr_url="https://github.com/org/repo/pull/99",
        owner="org",
        repo="repo",
        pr_number=99,
        status=ReviewStatus.RUNNING,
        current_stage="ai_review",
    )
    db_session.add(stale_review)
    db_session.commit()

    # Trigger lifespan startup with test session factory
    app.state.session_factory = lambda: db_session
    async with lifespan(app):
        # Startup ran
        pass

    db_session.expire_all()
    recovered = db_session.scalar(select(Review).where(Review.id == stale_id))
    assert recovered is not None
    assert recovered.status == ReviewStatus.FAILED
    assert recovered.error_code == "server_restart"
    assert "interrupted by an unexpected server restart" in recovered.error_message


# ==============================================================================
# DRILL E: Rate Limit (5 requests/min limit)
# ==============================================================================
def test_drill_e_rate_limit_sixth_request_rejected(client: TestClient):
    """Drill E: Verify 6th POST /api/reviews request triggers HTTP 429."""
    from unittest.mock import patch

    limiter.reset()
    try:
        with patch("app.api.reviews.run_review", new_callable=AsyncMock):
            payload = {"pr_url": "https://github.com/org/repo/pull/10"}
            for _ in range(5):
                res = client.post("/api/reviews", json=payload)
                assert res.status_code == 202

            # 6th request rejected
            res6 = client.post("/api/reviews", json=payload)
            assert res6.status_code == 429
            data = res6.json()
            assert data["error"]["code"] == "rate_limit_exceeded"
            assert "Retry-After" in res6.headers
    finally:
        limiter.reset()
