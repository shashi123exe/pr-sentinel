"""End-to-end integration tests for Phase 7 validation and persistence."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.ai.gemini_client import FakeGeminiClient
from app.analysis.runner import StaticAnalysisRunner
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
from app.graph.builder import build_graph
from app.graph.nodes import GraphDependencies
from app.schemas.ai import AIFinding, AIReviewResponse
from app.schemas.analysis import StaticAnalysisResult
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot


def sample_pr_meta() -> PRMetadata:
    """Return test PRMetadata."""
    return PRMetadata(
        title="Implement security auth handler",
        author="alice",
        base_sha="base1234567890abcdef1234567890abcdef12",
        head_sha="head1234567890abcdef1234567890abcdef12",
        base_ref="main",
        head_ref="feature/auth",
        state="open",
        html_url="https://github.com/owner/repo/pull/42",
        changed_files=1,
        additions=2,
        deletions=0,
    )


def sample_changed_file() -> ChangedFile:
    """Return test ChangedFile with unified diff."""
    patch_str = (
        "@@ -1,2 +1,4 @@\n"
        " def check_auth():\n"
        "+    token = request.headers.get('token')\n"
        "+    eval(token)\n"
        "     return True"
    )
    return ChangedFile(
        sha="sha1234567890abcdef1234567890abcdef12",
        path="app/auth.py",
        status="modified",
        additions=2,
        deletions=0,
        changes=2,
        patch=patch_str,
    )


@pytest.fixture
def mock_github():
    """Create a mock GitHubClient."""
    client = AsyncMock()
    client.fetch_pr_snapshot.return_value = PRSnapshot(
        metadata=sample_pr_meta(),
        files=[sample_changed_file()],
        truncated=False,
    )
    client.get_file_content.return_value = (
        "def check_auth():\n"
        "    token = request.headers.get('token')\n"
        "    eval(token)\n"
        "    return True\n"
    )
    return client


@pytest.mark.asyncio
async def test_phase7_end_to_end_validation_and_persistence(test_engine, mock_github):
    """Verify full graph with 3 AI findings: 1 valid, 1 fabricated, 1 missing file.

    Expectations:
    - Exactly 1 finding is accepted and persisted.
    - Exactly 2 findings are dropped.
    - review.status == ReviewStatus.COMPLETED
    - review.dropped_findings_count == 2
    - Persisted finding has status 'open', correct fingerprint, and matches f_valid.
    """
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "phase7-e2e-rev-1"

    with session_factory() as db:
        review = Review(
            id=review_id,
            pr_url="https://github.com/owner/repo/pull/42",
            owner="owner",
            repo="repo",
            pr_number=42,
            status=ReviewStatus.QUEUED,
            current_stage="queued",
            warnings=[],
        )
        db.add(review)
        db.commit()

    # 1. Valid finding: real processed file, valid line, genuine diff evidence
    f_valid = AIFinding(
        title="Remote code execution with eval",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="eval(token)",
        explanation="Dangerous eval on unvalidated header token.",
        suggested_fix="Parse token safely without code evaluation.",
        confidence=0.98,
    )

    # 2. Fabricated evidence finding: real file and line, but made-up evidence
    f_fabricated = AIFinding(
        title="Fabricated SQL injection",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="cursor.execute(f'SELECT * FROM users WHERE token = {token}')",
        explanation="Untrusted query interpolation",
        suggested_fix="Use parameterized queries",
        confidence=0.90,
    )

    # 3. Nonexistent file finding: references a file path not in the PR
    f_nonexistent_file = AIFinding(
        title="Hardcoded AWS credentials",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/credentials/aws.py",
        line_start=10,
        line_end=10,
        evidence="AWS_SECRET_KEY = 'AKIAIOSFODNN7EXAMPLE'",
        explanation="AWS credentials hardcoded in source file",
        confidence=0.95,
    )

    fake_ai = FakeGeminiClient(
        responses=[
            AIReviewResponse(
                findings=[f_valid, f_fabricated, f_nonexistent_file],
                summary="Detected 1 valid issue and 2 fabricated findings.",
            )
        ]
    )

    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[], tool_errors=[], tools_run=[]
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

    # Execute complete review graph workflow
    final_state = await graph.ainvoke(
        {
            "review_id": review_id,
            "pr_url": "https://github.com/owner/repo/pull/42",
            "errors": [],
            "warnings": [],
            "failed": False,
        }
    )

    assert final_state["failed"] is False
    assert final_state.get("dropped_findings_count") == 2
    assert len(final_state.get("validated_findings", [])) == 1

    # Verify database persistence
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev is not None
        assert rev.status == ReviewStatus.COMPLETED
        assert rev.current_stage == "completed"
        assert rev.dropped_findings_count == 2
        assert rev.completed_at is not None

        # Verify warnings captured drop reasons
        assert any(
            "Validation accepted 1 finding(s), dropped 2 finding(s)" in w
            for w in rev.warnings
        )
        assert any("nonexistent_file: 1" in w for w in rev.warnings)
        assert any("unverifiable_evidence: 1" in w for w in rev.warnings)

        # Verify persisted findings in database
        db_findings = db.scalars(
            select(Finding).where(Finding.review_id == review_id)
        ).all()
        assert len(db_findings) == 1

        persisted = db_findings[0]
        assert persisted.title == "Remote code execution with eval"
        assert persisted.severity == Severity.CRITICAL
        assert persisted.category == Category.SECURITY
        assert persisted.source == Source.AI
        assert persisted.status == FindingStatus.OPEN
        assert persisted.file_path == "app/auth.py"
        assert persisted.line_start == 3
        assert persisted.evidence == "eval(token)"
        assert persisted.confidence == 0.98
        assert len(persisted.fingerprint) == 64

        # Verify touched file
        db_files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == review_id)
        ).all()
        assert len(db_files) == 1
        assert db_files[0].path == "app/auth.py"
