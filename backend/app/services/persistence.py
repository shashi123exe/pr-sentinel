"""Idempotent database persistence service for review results."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
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
from app.schemas.diff import ProcessedFile

logger = logging.getLogger(__name__)


def save_review_results(
    session: Session,
    review_id: str,
    files: list[dict[str, Any] | ProcessedFile],
    findings: list[dict[str, Any]],
    usage: dict[str, Any],
    truncation: dict[str, Any],
    warnings: list[str],
    dropped_findings_count: int = 0,
) -> None:
    """Persist review files, findings, token usage, truncation, and warnings atomically.

    Guarantees:
    - Atomicity: Executed in a single database transaction; rolls back on error.
    - Idempotency: Cleans up any existing results for review_id before insert.
    - Safe Status Initialization: All created findings are set to status="open".
    """
    try:
        review = session.scalar(select(Review).where(Review.id == review_id))
        if not review:
            logger.warning(
                "Review record '%s' not found during persistence (deleted); skipping.",
                review_id,
            )
            return

        # 1. Query existing findings to preserve user triage decisions
        # (approved/dismissed)
        existing_findings = session.scalars(
            select(Finding).where(Finding.review_id == review_id)
        ).all()
        triage_by_fp = {
            f.fingerprint: (f.status, f.updated_at) for f in existing_findings
        }

        # Clean existing files and findings to support idempotent re-runs
        session.execute(delete(Finding).where(Finding.review_id == review_id))
        session.execute(delete(ReviewFile).where(ReviewFile.review_id == review_id))

        # 2. Insert ReviewFile records
        for item in files:
            if isinstance(item, ProcessedFile):
                rf = ReviewFile(
                    review_id=review_id,
                    path=item.path,
                    status=item.status,
                    additions=item.additions,
                    deletions=item.deletions,
                    patch=item.patch,
                    language=item.language,
                    skipped_reason=item.skipped_reason,
                )
            else:
                rf = ReviewFile(
                    review_id=review_id,
                    path=item["path"],
                    status=item.get("status", "modified"),
                    additions=item.get("additions", 0),
                    deletions=item.get("deletions", 0),
                    patch=item.get("patch"),
                    language=item.get("language"),
                    skipped_reason=item.get("skipped_reason"),
                )
            session.add(rf)

        # 3. Insert Finding records
        for f_data in findings:
            sev = (
                f_data["severity"]
                if isinstance(f_data["severity"], Severity)
                else Severity(str(f_data["severity"]).lower())
            )
            cat = (
                f_data["category"]
                if isinstance(f_data["category"], Category)
                else Category(str(f_data["category"]).lower())
            )
            src = (
                f_data["source"]
                if isinstance(f_data["source"], Source)
                else Source(str(f_data["source"]).lower())
            )

            fp = f_data["fingerprint"]
            triage_info = triage_by_fp.get(fp)
            initial_status = triage_info[0] if triage_info else FindingStatus.OPEN
            initial_updated_at = triage_info[1] if triage_info else datetime.now(UTC)

            finding_rec = Finding(
                review_id=review_id,
                file_path=f_data["file_path"],
                line_start=f_data["line_start"],
                line_end=f_data.get("line_end", f_data["line_start"]),
                title=f_data["title"],
                severity=sev,
                category=cat,
                source=src,
                tool_name=f_data.get("tool_name", "unknown"),
                rule_id=f_data.get("rule_id", "rule"),
                evidence=f_data.get("evidence", ""),
                explanation=f_data.get("explanation", ""),
                suggested_fix=f_data.get("suggested_fix"),
                confidence=float(f_data.get("confidence", 1.0)),
                fingerprint=fp,
                status=initial_status,
                updated_at=initial_updated_at,
            )
            session.add(finding_rec)

        # 4. Update Review summary and metadata
        review.status = ReviewStatus.COMPLETED
        review.current_stage = "completed"
        review.completed_at = datetime.now(UTC)
        review.truncated = truncation.get("truncated", False)
        review.llm_model = usage.get("model", "")
        review.llm_tokens_in = usage.get("in", 0)
        review.llm_tokens_out = usage.get("out", 0)
        review.warnings = warnings
        review.dropped_findings_count = dropped_findings_count

        session.commit()
        logger.info(
            "Persisted review [%s] results: %d files, %d findings, %d dropped.",
            review_id,
            len(files),
            len(findings),
            dropped_findings_count,
        )

    except Exception as exc:
        session.rollback()
        logger.error(
            "Transaction rollback in save_review_results for [%s]: %s",
            review_id,
            exc,
        )
        raise
