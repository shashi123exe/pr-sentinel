"""Unit tests for atomic idempotent review persistence service."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

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
from app.services.persistence import save_review_results


def _create_review(db: Session) -> str:
    """Helper to create a queued review record."""
    rev_id = str(uuid.uuid4())
    rev = Review(
        id=rev_id,
        pr_url="https://github.com/owner/repo/pull/1",
        owner="owner",
        repo="repo",
        pr_number=1,
        status=ReviewStatus.RUNNING,
        current_stage="persist_results",
    )
    db.add(rev)
    db.commit()
    return rev_id


def test_save_review_results_success(test_engine):
    """Verify successful atomic persistence of review results."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with session_factory() as db:
        rev_id = _create_review(db)

    files = [
        ProcessedFile(
            path="app/main.py",
            language="python",
            status="modified",
            additions=10,
            deletions=2,
            patch="@@ -1,2 +1,3 @@",
        ),
        ProcessedFile(
            path="package-lock.json",
            language="json",
            status="modified",
            additions=50,
            deletions=50,
            skipped_reason="lockfile",
        ),
    ]

    findings = [
        {
            "file_path": "app/main.py",
            "line_start": 5,
            "line_end": 5,
            "title": "SQL Injection",
            "severity": Severity.HIGH.value,
            "category": Category.SECURITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "ai_review",
            "evidence": "db.execute(sql)",
            "explanation": "Dangerous formatting",
            "suggested_fix": "Use params",
            "confidence": 0.95,
            "fingerprint": "fp_sql_injection",
        }
    ]

    token_usage = {"in": 500, "out": 250, "model": "gemini-2.5-pro"}
    warnings = ["Validation dropped 1 finding(s)"]

    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=files,
            findings=findings,
            usage=token_usage,
            truncation={"truncated": False},
            warnings=warnings,
            dropped_findings_count=1,
        )

    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == rev_id))
        assert rev is not None
        assert rev.status == ReviewStatus.COMPLETED
        assert rev.completed_at is not None
        assert rev.llm_model == "gemini-2.5-pro"
        assert rev.llm_tokens_in == 500
        assert rev.llm_tokens_out == 250
        assert rev.dropped_findings_count == 1
        assert rev.warnings == warnings

        db_files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == rev_id)
        ).all()
        assert len(db_files) == 2

        db_findings = db.scalars(
            select(Finding).where(Finding.review_id == rev_id)
        ).all()
        assert len(db_findings) == 1
        f = db_findings[0]
        assert f.status == FindingStatus.OPEN
        assert f.title == "SQL Injection"
        assert f.fingerprint == "fp_sql_injection"


def test_idempotent_rerun_persistence(test_engine):
    """Verify re-running persistence replaces existing results without error."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with session_factory() as db:
        rev_id = _create_review(db)

    file_v1 = [
        ProcessedFile(
            path="app/auth.py",
            language="python",
            status="modified",
            additions=5,
            deletions=1,
            patch="@@",
        )
    ]
    finding_v1 = [
        {
            "file_path": "app/auth.py",
            "line_start": 1,
            "line_end": 1,
            "title": "Issue V1",
            "severity": Severity.LOW.value,
            "category": Category.CODE_QUALITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "ai_review",
            "evidence": "v1",
            "explanation": "Explanation V1",
            "confidence": 0.8,
            "fingerprint": "fp_rerun",
        }
    ]

    # First persistence run
    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=file_v1,
            findings=finding_v1,
            usage={"in": 100, "out": 50, "model": "gemini-2.5-pro"},
            truncation={"truncated": False},
            warnings=[],
            dropped_findings_count=0,
        )

    # Second persistence run (updated finding and files)
    file_v2 = [
        ProcessedFile(
            path="app/auth.py",
            language="python",
            status="modified",
            additions=15,
            deletions=3,
            patch="@@",
        )
    ]
    finding_v2 = [
        {
            "file_path": "app/auth.py",
            "line_start": 2,
            "line_end": 2,
            "title": "Issue V2 Updated",
            "severity": Severity.HIGH.value,
            "category": Category.SECURITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "ai_review",
            "evidence": "v2",
            "explanation": "Explanation V2 Updated",
            "confidence": 0.95,
            "fingerprint": "fp_rerun",
        }
    ]

    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=file_v2,
            findings=finding_v2,
            usage={"in": 200, "out": 100, "model": "gemini-2.5-pro"},
            truncation={"truncated": False},
            warnings=["Updated"],
            dropped_findings_count=0,
        )

    with session_factory() as db:
        files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == rev_id)
        ).all()
        assert len(files) == 1
        assert files[0].additions == 15

        findings = db.scalars(select(Finding).where(Finding.review_id == rev_id)).all()
        assert len(findings) == 1
        assert findings[0].title == "Issue V2 Updated"
        assert findings[0].severity == Severity.HIGH


def test_persistence_rollback_on_error(test_engine):
    """Verify transaction rollback on error leaves no partial records."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with session_factory() as db:
        rev_id = _create_review(db)

    # Malformed finding missing mandatory 'severity' key to trigger error
    bad_findings = [
        {
            "file_path": "app/bad.py",
            "line_start": 1,
            "line_end": 1,
            "title": "Bad Finding",
            # missing severity
            "category": Category.SECURITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "ai_review",
            "evidence": "x",
            "explanation": "y",
            "confidence": 0.8,
            "fingerprint": "fp_bad",
        }
    ]

    files = [
        ProcessedFile(
            path="app/bad.py",
            language="python",
            status="modified",
            additions=1,
            deletions=0,
            patch="@@",
        )
    ]

    with pytest.raises((KeyError, ValueError)):
        with session_factory() as db:
            save_review_results(
                session=db,
                review_id=rev_id,
                files=files,
                findings=bad_findings,
                usage={"in": 10, "out": 10, "model": "m"},
                truncation={"truncated": False},
                warnings=[],
                dropped_findings_count=0,
            )

    with session_factory() as db:
        # Review status must remain unchanged from RUNNING
        rev = db.scalar(select(Review).where(Review.id == rev_id))
        assert rev.status == ReviewStatus.RUNNING
        # No files or findings saved
        files_count = db.scalar(
            select(ReviewFile).where(ReviewFile.review_id == rev_id)
        )
        assert files_count is None


def test_sqlite_cascade_deletion(test_engine):
    """Verify deleting Review cascades to associated ReviewFiles and Findings."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with session_factory() as db:
        rev_id = _create_review(db)

    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=[
                ProcessedFile(
                    path="app/test.py",
                    language="python",
                    status="added",
                    additions=5,
                    deletions=0,
                    patch="@@",
                )
            ],
            findings=[
                {
                    "file_path": "app/test.py",
                    "line_start": 1,
                    "line_end": 1,
                    "title": "Cascade test",
                    "severity": Severity.LOW.value,
                    "category": Category.CODE_QUALITY.value,
                    "source": Source.AI.value,
                    "tool_name": "gemini",
                    "rule_id": "ai_review",
                    "evidence": "e",
                    "explanation": "exp",
                    "confidence": 0.9,
                    "fingerprint": "fp_cascade",
                }
            ],
            usage={"in": 1, "out": 1, "model": "m"},
            truncation={"truncated": False},
            warnings=[],
            dropped_findings_count=0,
        )

    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == rev_id))
        db.delete(rev)
        db.commit()

        # Children must be deleted
        remaining_files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == rev_id)
        ).all()
        assert len(remaining_files) == 0

        remaining_findings = db.scalars(
            select(Finding).where(Finding.review_id == rev_id)
        ).all()
        assert len(remaining_findings) == 0


def test_triage_preservation_on_rerun(test_engine):
    """Verify triage decisions (approved/dismissed) survive rerun persistence."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    with session_factory() as db:
        rev_id = _create_review(db)

    files = [
        ProcessedFile(
            path="app/auth.py",
            language="python",
            status="modified",
            additions=5,
            deletions=1,
            patch="@@",
        )
    ]
    findings = [
        {
            "file_path": "app/auth.py",
            "line_start": 10,
            "line_end": 10,
            "title": "Bug 1",
            "severity": Severity.HIGH.value,
            "category": Category.SECURITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "r1",
            "evidence": "ev1",
            "explanation": "exp1",
            "confidence": 0.9,
            "fingerprint": "fp_bug_1",
        },
        {
            "file_path": "app/auth.py",
            "line_start": 20,
            "line_end": 20,
            "title": "Bug 2",
            "severity": Severity.MEDIUM.value,
            "category": Category.SECURITY.value,
            "source": Source.AI.value,
            "tool_name": "gemini",
            "rule_id": "r2",
            "evidence": "ev2",
            "explanation": "exp2",
            "confidence": 0.85,
            "fingerprint": "fp_bug_2",
        },
    ]

    # 1. Initial persistence
    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=files,
            findings=findings,
            usage={"in": 10, "out": 10, "model": "m"},
            truncation={"truncated": False},
            warnings=[],
            dropped_findings_count=0,
        )

    # 2. User triages finding 1 as APPROVED, finding 2 as DISMISSED
    with session_factory() as db:
        f1 = db.scalar(
            select(Finding).where(
                Finding.review_id == rev_id, Finding.fingerprint == "fp_bug_1"
            )
        )
        f2 = db.scalar(
            select(Finding).where(
                Finding.review_id == rev_id, Finding.fingerprint == "fp_bug_2"
            )
        )
        assert f1 is not None and f2 is not None
        f1.status = FindingStatus.APPROVED
        f2.status = FindingStatus.DISMISSED
        db.commit()

    # 3. Rerun persistence (e.g. background retry or re-analysis)
    with session_factory() as db:
        save_review_results(
            session=db,
            review_id=rev_id,
            files=files,
            findings=findings,
            usage={"in": 10, "out": 10, "model": "m"},
            truncation={"truncated": False},
            warnings=[],
            dropped_findings_count=0,
        )

    # 4. Verify triage states are preserved
    with session_factory() as db:
        f1_after = db.scalar(
            select(Finding).where(
                Finding.review_id == rev_id, Finding.fingerprint == "fp_bug_1"
            )
        )
        f2_after = db.scalar(
            select(Finding).where(
                Finding.review_id == rev_id, Finding.fingerprint == "fp_bug_2"
            )
        )
        assert f1_after.status == FindingStatus.APPROVED
        assert f2_after.status == FindingStatus.DISMISSED


def test_save_review_results_when_review_deleted_concurrency(test_engine):
    """Verify background persistence handles deleted reviews gracefully."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    missing_rev_id = str(uuid.uuid4())

    with session_factory() as db:
        # Should not raise AppError(404) or crash; returns cleanly
        save_review_results(
            session=db,
            review_id=missing_rev_id,
            files=[],
            findings=[],
            usage={},
            truncation={},
            warnings=[],
            dropped_findings_count=0,
        )

        # No orphan records created
        orphan_files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == missing_rev_id)
        ).all()
        assert len(orphan_files) == 0
