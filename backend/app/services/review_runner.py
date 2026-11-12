"""Asynchronous review execution runner managing workflow invocation and safety."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Review, ReviewStatus
from app.db.session import SessionLocal
from app.graph.builder import get_compiled_graph
from app.graph.nodes import GraphDependencies
from app.graph.state import ReviewState

logger = logging.getLogger(__name__)

DEFAULT_REVIEW_TIMEOUT_SECONDS = 600.0  # 10 minutes


def _safety_mark_failed(
    session_factory: Callable[[], Session],
    review_id: str,
    error_code: str,
    error_message: str,
) -> None:
    """Fallback safety net ensuring a review never remains in running/queued state."""
    try:
        with session_factory() as db:
            review = db.scalar(select(Review).where(Review.id == review_id))
            if review and review.status in (ReviewStatus.QUEUED, ReviewStatus.RUNNING):
                review.status = ReviewStatus.FAILED
                review.completed_at = datetime.now(UTC)
                if not review.error_code:
                    review.error_code = error_code
                    review.error_message = error_message
                db.commit()
                logger.warning(
                    "Safety net marked review [%s] as failed: %s (%s)",
                    review_id,
                    error_code,
                    error_message,
                )
    except Exception as exc:
        logger.critical(
            "Fatal failure in safety net for review [%s]: %s",
            review_id,
            exc,
        )


async def run_review(
    review_id: str,
    deps: GraphDependencies | None = None,
    timeout_seconds: float = DEFAULT_REVIEW_TIMEOUT_SECONDS,
    compiled_app: Any = None,
) -> ReviewState:
    """Execute the full PR review LangGraph workflow with timeout and exception safety.

    Args:
        review_id: UUID string of the target review record.
        deps: Optional injected dependencies (for testing or custom clients).
        timeout_seconds: Max runtime before terminating the review as failed.
        compiled_app: Optional precompiled LangGraph application.

    Returns:
        The final workflow ReviewState dict.
    """
    active_deps = deps or GraphDependencies(session_factory=SessionLocal)

    try:
        # 1. Verify review existence and retrieve canonical PR URL
        with active_deps.session_factory() as db:
            review = db.scalar(select(Review).where(Review.id == review_id))
            if not review:
                logger.error(
                    "Cannot run review [%s]: record not found in database.",
                    review_id,
                )
                return {
                    "review_id": review_id,
                    "failed": True,
                    "errors": [
                        {
                            "code": "review_not_found",
                            "message": "Review record not found.",
                            "stage": "init",
                        }
                    ],
                }
            pr_url = review.pr_url

        # 2. Resolve compiled LangGraph state machine (compiled once per application)
        workflow_app = compiled_app or get_compiled_graph()

        # 3. Construct initial execution state
        initial_state: ReviewState = {
            "review_id": review_id,
            "pr_url": pr_url,
            "parsed_pr": None,
            "pr_meta": None,
            "changed_files": [],
            "processed_files": [],
            "hunks": [],
            "static_findings": [],
            "tool_errors": [],
            "context_snippets": [],
            "raw_ai_findings": [],
            "validated_findings": [],
            "errors": [],
            "token_usage": {"in": 0, "out": 0, "model": ""},
            "truncation": {"truncated": False, "reasons": []},
            "warnings": [],
            "failed": False,
        }

        # 4. Invoke graph execution under timeout and safety net
        try:
            logger.info(
                "Starting workflow execution for review [%s] (timeout: %.1fs)",
                review_id,
                timeout_seconds,
            )
            final_state: ReviewState = await asyncio.wait_for(
                workflow_app.ainvoke(
                    initial_state,
                    config={"configurable": {"deps": active_deps}},
                ),
                timeout=timeout_seconds,
            )
            return final_state

        except TimeoutError:
            logger.error(
                "Review [%s] timed out after %.1f seconds.",
                review_id,
                timeout_seconds,
            )
            _safety_mark_failed(
                active_deps.session_factory,
                review_id,
                error_code="review_timeout",
                error_message=(
                    f"Review execution exceeded the maximum timeout of "
                    f"{int(timeout_seconds)} seconds."
                ),
            )
            return {
                "review_id": review_id,
                "failed": True,
                "errors": [
                    {
                        "code": "review_timeout",
                        "message": "Review execution timed out.",
                        "stage": "runner",
                    }
                ],
            }

        except Exception as exc:
            logger.error(
                "Uncaught exception in review runner for [%s]: %s",
                review_id,
                exc,
            )
            _safety_mark_failed(
                active_deps.session_factory,
                review_id,
                error_code="internal_runner_error",
                error_message="An unexpected error occurred during review execution.",
            )
            return {
                "review_id": review_id,
                "failed": True,
                "errors": [
                    {
                        "code": "internal_runner_error",
                        "message": (
                            "An unexpected error occurred during review execution."
                        ),
                        "stage": "runner",
                    }
                ],
            }
    finally:
        await active_deps.aclose()
