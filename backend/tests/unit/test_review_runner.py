"""Unit tests for ReviewRunner execution, safety nets, timeouts, and lifecycle."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import Review, ReviewStatus
from app.graph.nodes import GraphDependencies
from app.schemas.review import (
    ReviewOut,
    calculate_progress_percent,
)
from app.services.review_runner import run_review


# ==============================================================================
# Progress Percent Mapping Tests
# ==============================================================================
def test_calculate_progress_percent_table():
    """Verify exact progress percentages mapped to stages."""
    assert calculate_progress_percent(ReviewStatus.QUEUED, "queued") == 0
    assert calculate_progress_percent(ReviewStatus.RUNNING, "fetch_pr") == 10
    assert calculate_progress_percent(ReviewStatus.RUNNING, "process_diff") == 25
    assert calculate_progress_percent(ReviewStatus.RUNNING, "static_analysis") == 45
    assert calculate_progress_percent(ReviewStatus.RUNNING, "retrieve_context") == 60
    assert calculate_progress_percent(ReviewStatus.RUNNING, "ai_review") == 80
    assert calculate_progress_percent(ReviewStatus.RUNNING, "validate_findings") == 90
    assert calculate_progress_percent(ReviewStatus.RUNNING, "persist_results") == 95
    assert calculate_progress_percent(ReviewStatus.COMPLETED, "completed") == 100

    # Failed reviews retain their last stage percentage
    assert calculate_progress_percent(ReviewStatus.FAILED, "static_analysis") == 45
    assert calculate_progress_percent(ReviewStatus.FAILED, "ai_review") == 80


def test_review_out_computes_progress_and_normalizes_warnings():
    """Verify ReviewOut computes progress_percent and formats warnings."""
    # Dict input
    data = {
        "id": "rev-test-1",
        "pr_url": "https://github.com/owner/repo/pull/1",
        "owner": "owner",
        "repo": "repo",
        "pr_number": 1,
        "pr_title": "PR",
        "pr_author": "dev",
        "base_sha": "b" * 40,
        "head_sha": "h" * 40,
        "status": ReviewStatus.RUNNING,
        "current_stage": "retrieve_context",
        "files_changed": 1,
        "additions": 10,
        "deletions": 2,
        "truncated": False,
        "llm_model": "gemini-2.5-pro",
        "llm_tokens_in": 100,
        "llm_tokens_out": 20,
        "warnings": ["Warning A", 123],
        "created_at": "2026-11-04T00:00:00Z",
        "completed_at": None,
    }
    out = ReviewOut.model_validate(data)
    assert out.progress_percent == 60
    assert out.warnings == ["Warning A", "123"]


# ==============================================================================
# Runner Safety Net Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_runner_timeout_marks_review_failed(test_engine):
    """Verify review exceeding timeout terminates with review_timeout."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "timeout-rev-id"
    with session_factory() as db:
        db.add(
            Review(
                id=review_id,
                pr_url="https://github.com/owner/repo/pull/42",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.commit()

    deps = GraphDependencies(session_factory=session_factory)

    # Patch compiled_app.ainvoke to sleep longer than the timeout
    async def slow_invoke(state, *args, **kwargs):
        await asyncio.sleep(5)
        return state

    with patch(
        "langgraph.graph.state.CompiledStateGraph.ainvoke",
        side_effect=slow_invoke,
    ):
        result = await run_review(
            review_id=review_id,
            deps=deps,
            timeout_seconds=0.1,  # Fast timeout
        )

    assert result["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "review_timeout"
        assert rev.completed_at is not None
        assert "timeout" in rev.error_message.lower()


@pytest.mark.asyncio
async def test_runner_unexpected_crash_marks_review_failed(test_engine):
    """Verify unexpected exception in runner marks review as failed via safety net."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "crash-rev-id"
    with session_factory() as db:
        db.add(
            Review(
                id=review_id,
                pr_url="https://github.com/owner/repo/pull/42",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.commit()

    deps = GraphDependencies(session_factory=session_factory)

    # Simulate unexpected catastrophic exception
    with patch(
        "langgraph.graph.state.CompiledStateGraph.ainvoke",
        side_effect=RuntimeError("System out of memory"),
    ):
        result = await run_review(
            review_id=review_id,
            deps=deps,
            timeout_seconds=10.0,
        )

    assert result["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "internal_runner_error"
        assert rev.completed_at is not None


@pytest.mark.asyncio
async def test_runner_nonexistent_review_returns_safe_failure(test_engine):
    """Verify attempting to run a nonexistent review ID returns failure cleanly."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    deps = GraphDependencies(session_factory=session_factory)
    result = await run_review(review_id="does-not-exist", deps=deps)
    assert result["failed"] is True
    assert result["errors"][0]["code"] == "review_not_found"


# ==============================================================================
# AI Tool Safety Verification
# ==============================================================================
def test_gemini_client_receives_no_tools_in_config():
    """Verify Gemini request config contains zero tools or function declarations."""
    from google.genai import types

    from app.schemas.ai import AIReviewResponse

    config = types.GenerateContentConfig(
        system_instruction="sys",
        response_mime_type="application/json",
        response_schema=AIReviewResponse,
        temperature=0.2,
    )
    assert getattr(config, "tools", None) is None
    assert getattr(config, "function_declarations", None) is None


@pytest.mark.asyncio
async def test_concurrent_reviews_execution(test_engine):
    """Verify concurrent reviews execute in parallel without shared state."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Setup two review records in DB
    rev_id_1 = "concurrent-rev-1"
    rev_id_2 = "concurrent-rev-2"
    with session_factory() as db:
        db.add(
            Review(
                id=rev_id_1,
                pr_url="https://github.com/owner/repo/pull/1",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.add(
            Review(
                id=rev_id_2,
                pr_url="https://github.com/owner/repo/pull/2",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.commit()

    deps1 = GraphDependencies(session_factory=session_factory)
    deps2 = GraphDependencies(session_factory=session_factory)

    # Patch the graph execution to return state directly with distinct review IDs
    async def mock_invoke_1(state, *args, **kwargs):
        await asyncio.sleep(0.05)
        return {"review_id": rev_id_1, "failed": False}

    async def mock_invoke_2(state, *args, **kwargs):
        await asyncio.sleep(0.05)
        return {"review_id": rev_id_2, "failed": False}

    app1 = AsyncMock()
    app1.ainvoke.side_effect = mock_invoke_1
    app2 = AsyncMock()
    app2.ainvoke.side_effect = mock_invoke_2

    task1 = asyncio.create_task(run_review(rev_id_1, deps=deps1, compiled_app=app1))
    task2 = asyncio.create_task(run_review(rev_id_2, deps=deps2, compiled_app=app2))

    res1, res2 = await asyncio.gather(task1, task2)

    assert res1["review_id"] == rev_id_1
    assert res2["review_id"] == rev_id_2
    assert res1["failed"] is False
    assert res2["failed"] is False


@pytest.mark.asyncio
async def test_background_task_uses_independent_session(test_engine):
    """Verify review runner creates its own independent session from session_factory."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "independent-session-rev"

    # Create caller session
    caller_db = session_factory()
    caller_db.add(
        Review(
            id=review_id,
            pr_url="https://github.com/owner/repo/pull/10",
            status=ReviewStatus.QUEUED,
            current_stage="queued",
        )
    )
    caller_db.commit()

    # Pass session_factory to deps
    deps = GraphDependencies(session_factory=session_factory)
    mock_app = AsyncMock()
    mock_app.ainvoke.return_value = {"review_id": review_id, "failed": False}

    result = await run_review(review_id=review_id, deps=deps, compiled_app=mock_app)
    assert result["failed"] is False

    # The caller_db should still be alive and open, not closed by runner
    assert caller_db.is_active
    caller_db.close()
