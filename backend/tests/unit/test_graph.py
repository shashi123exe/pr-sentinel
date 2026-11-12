"""Unit and workflow tests for Phase 6 LangGraph orchestration state machine."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.ai.gemini_client import FakeGeminiClient
from app.analysis.runner import StaticAnalysisRunner
from app.db.models import (
    Category,
    Finding,
    Review,
    ReviewFile,
    ReviewStatus,
    Severity,
    Source,
)
from app.errors import AppError
from app.graph.builder import build_graph
from app.graph.nodes import GraphDependencies
from app.graph.state import ReviewState
from app.schemas.ai import AIFinding, AIReviewResponse
from app.schemas.analysis import StaticAnalysisResult, StaticFinding
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot


def sample_pr_meta() -> PRMetadata:
    """Return test PRMetadata."""
    return PRMetadata(
        title="Add authentication middleware",
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
def mock_github_client():
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


@pytest.fixture(autouse=True)
def hermetic_graph_test_environment(monkeypatch):
    """Enforce complete independence from ambient Gemini credentials."""
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_MODEL", "")


# ==============================================================================
# Scenario A: Graph Happy Path
# ==============================================================================
@pytest.mark.asyncio
async def test_graph_happy_path(test_engine, mock_github_client):
    """Verify complete end-to-end review execution across all LangGraph nodes."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # 1. Setup queued review in database
    review_id = "happy-path-rev-1"
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

    # 2. Fake Gemini client with a valid finding
    ai_finding = AIFinding(
        title="Remote code execution with eval",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="eval(token)",
        explanation="Untrusted token passed directly to eval.",
        suggested_fix="Avoid eval; parse JSON or validate token.",
        confidence=0.98,
    )
    fake_ai = FakeGeminiClient(
        responses=[AIReviewResponse(findings=[ai_finding])],
        tokens_in=450,
        tokens_out=85,
    )

    # 3. Mock Static Analysis Runner with a Bandit finding
    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[
            StaticFinding(
                file_path="app/auth.py",
                line_start=3,
                line_end=3,
                title="[B307] Use of possibly insecure function - eval",
                severity=Severity.HIGH,
                category=Category.SECURITY,
                tool_name="bandit",
                rule_id="B307",
                evidence="eval(token)",
                explanation="eval detected.",
            )
        ],
        tool_errors=[],
        tools_run=["bandit", "ruff"],
    )

    # 4. Construct dependencies and compile graph
    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

    initial_state: ReviewState = {
        "review_id": review_id,
        "pr_url": "https://github.com/owner/repo/pull/42",
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

    final_state = await graph.ainvoke(initial_state)

    # 5. Assertions on final LangGraph state
    assert final_state["failed"] is False
    assert len(final_state["validated_findings"]) == 2  # 1 AI + 1 Static
    assert final_state["token_usage"]["in"] == 450
    assert final_state["token_usage"]["out"] == 85

    # 6. Assertions on persisted database records
    with session_factory() as db:
        persisted_rev = db.scalar(select(Review).where(Review.id == review_id))
        assert persisted_rev is not None
        assert persisted_rev.status == ReviewStatus.COMPLETED
        assert persisted_rev.current_stage == "completed"
        assert persisted_rev.completed_at is not None
        assert persisted_rev.pr_title == "Add authentication middleware"
        assert persisted_rev.pr_author == "alice"
        assert persisted_rev.llm_tokens_in == 450
        assert persisted_rev.llm_tokens_out == 85

        # Check ReviewFile records
        files = db.scalars(
            select(ReviewFile).where(ReviewFile.review_id == review_id)
        ).all()
        assert len(files) == 1
        assert files[0].path == "app/auth.py"
        assert files[0].additions == 2
        assert files[0].skipped_reason is None

        # Check Finding records
        findings = db.scalars(
            select(Finding).where(Finding.review_id == review_id)
        ).all()
        assert len(findings) == 2
        sources = {f.source for f in findings}
        assert Source.AI in sources
        assert Source.STATIC in sources

        # Every finding must have a non-empty unique fingerprint
        fps = [f.fingerprint for f in findings]
        assert len(set(fps)) == 2
        assert all(len(fp) == 64 for fp in fps)


# ==============================================================================
# Scenario B: Failure Scenarios
# ==============================================================================
@pytest.mark.asyncio
async def test_failure_github_pr_not_found_routes_to_fail_review(test_engine):
    """Verify GitHub 404 routes directly to fail_review and marks review failed."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "fail-404-rev"
    with session_factory() as db:
        db.add(
            Review(
                id=review_id,
                pr_url="https://github.com/owner/repo/pull/999",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.commit()

    gh_client = AsyncMock()
    gh_client.fetch_pr_snapshot.side_effect = AppError(
        code="pr_not_found",
        message="Pull request not found.",
        status_code=404,
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=gh_client,
    )
    graph = build_graph(deps=deps).compile()

    final_state = await graph.ainvoke(
        {
            "review_id": review_id,
            "pr_url": "https://github.com/owner/repo/pull/999",
            "errors": [],
            "failed": False,
        }
    )

    assert final_state["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "pr_not_found"
        assert rev.error_message == "Pull request not found."
        assert rev.current_stage == "fetch_pr"


@pytest.mark.asyncio
async def test_failure_github_auth_failed_routes_to_fail_review(test_engine):
    """Verify GitHub authentication failure sets safe error and marks failed."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "fail-auth-rev"
    with session_factory() as db:
        db.add(
            Review(
                id=review_id,
                pr_url="https://github.com/owner/repo/pull/1",
                status=ReviewStatus.QUEUED,
                current_stage="queued",
            )
        )
        db.commit()

    gh_client = AsyncMock()
    gh_client.fetch_pr_snapshot.side_effect = AppError(
        code="github_auth_failed",
        message="Invalid GitHub credentials.",
        status_code=401,
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=gh_client,
    )
    graph = build_graph(deps=deps).compile()

    final_state = await graph.ainvoke(
        {
            "review_id": review_id,
            "pr_url": "https://github.com/owner/repo/pull/1",
            "errors": [],
            "failed": False,
        }
    )

    assert final_state["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "github_auth_failed"


@pytest.mark.asyncio
async def test_static_tool_failure_is_non_fatal(test_engine, mock_github_client):
    """Verify static analysis tool error records a warning and review completes."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "static-warn-rev"
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

    # Bandit succeeds, but ESLint failed
    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[
            StaticFinding(
                file_path="app/auth.py",
                line_start=3,
                line_end=3,
                title="Use of eval",
                severity=Severity.HIGH,
                category=Category.SECURITY,
                tool_name="bandit",
                rule_id="B307",
                evidence="eval(token)",
                explanation="eval detected.",
            )
        ],
        tool_errors=["ESLint failed: Node.js runtime not found"],
        tools_run=["bandit"],
    )

    fake_ai = FakeGeminiClient(responses=[AIReviewResponse(findings=[])])

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

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
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.COMPLETED
        # Warnings must contain the tool error
        assert any("ESLint failed" in w for w in rev.warnings)


# ==============================================================================
# Scenario C: AI-Unavailable Policy (Case 1 and Case 2)
# ==============================================================================
@pytest.mark.asyncio
async def test_ai_unavailable_policy_case_1_with_static_findings(
    test_engine, mock_github_client
):
    """Case 1: AI fails but static findings exist -> workflow completes with warning."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "ai-unavail-case-1"
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

    # Static analysis returns 1 finding
    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[
            StaticFinding(
                file_path="app/auth.py",
                line_start=3,
                line_end=3,
                title="Hardcoded secret",
                severity=Severity.HIGH,
                category=Category.SECURITY,
                tool_name="bandit",
                rule_id="B105",
                evidence="secret = '123'",
                explanation="Static finding",
            )
        ],
        tool_errors=[],
        tools_run=["bandit"],
    )

    # Gemini client fails with unavailable
    fake_ai = FakeGeminiClient(
        errors=[
            AppError(
                code="ai_unavailable",
                message="Gemini API is down.",
                status_code=503,
            )
        ]
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

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
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.COMPLETED
        assert rev.current_stage == "completed"
        # Warning recorded
        assert any("AI review unavailable" in w for w in rev.warnings)
        # Static finding persisted
        findings = db.scalars(
            select(Finding).where(Finding.review_id == review_id)
        ).all()
        assert len(findings) == 1
        assert findings[0].title == "Hardcoded secret"


@pytest.mark.asyncio
async def test_ai_unavailable_policy_case_2_without_static_findings(
    test_engine, mock_github_client
):
    """Case 2: AI fails and NO static findings exist -> review fails safely."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "ai-unavail-case-2"
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

    # Zero static findings
    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[],
        tool_errors=[],
        tools_run=["bandit", "ruff"],
    )

    # Gemini client fails
    fake_ai = FakeGeminiClient(
        errors=[
            AppError(
                code="ai_not_configured",
                message="GEMINI_API_KEY missing.",
                status_code=400,
            )
        ]
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

    final_state = await graph.ainvoke(
        {
            "review_id": review_id,
            "pr_url": "https://github.com/owner/repo/pull/42",
            "errors": [],
            "warnings": [],
            "failed": False,
        }
    )

    assert final_state["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "ai_not_configured"


# ==============================================================================
# Scenario D: Finding Validation (Malformed & Out-of-hunk Dropping)
# ==============================================================================
@pytest.mark.asyncio
async def test_validate_findings_drops_unreviewed_and_out_of_bounds_files(
    test_engine, mock_github_client
):
    """Verify validation drops findings for unreviewed files or out-of-hunk lines."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "validate-drop-rev"
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

    # Return 2 findings: 1 valid, 1 referencing an unreviewed file, 1 out of bounds
    f_valid = AIFinding(
        title="Valid Finding",
        severity=Severity.MEDIUM,
        category=Category.LOGIC_BUG,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="eval(token)",
        explanation="Valid",
        confidence=0.9,
    )
    f_unreviewed_file = AIFinding(
        title="Unreviewed file finding",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        file_path="app/unreviewed.py",
        line_start=1,
        line_end=1,
        evidence="pass",
        explanation="Not reviewed",
        confidence=0.8,
    )
    f_out_of_bounds_line = AIFinding(
        title="Out of bounds line",
        severity=Severity.LOW,
        category=Category.CODE_QUALITY,
        file_path="app/auth.py",
        line_start=9999,  # Line 9999 is outside the hunk range [1-4]
        line_end=9999,
        evidence="old code",
        explanation="Out of bounds",
        confidence=0.8,
    )

    fake_ai = FakeGeminiClient(
        responses=[
            AIReviewResponse(
                findings=[f_valid, f_unreviewed_file, f_out_of_bounds_line]
            )
        ]
    )

    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[], tool_errors=[], tools_run=[]
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

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
    # Only 1 valid finding should survive
    assert len(final_state["validated_findings"]) == 1
    assert final_state["validated_findings"][0]["title"] == "Valid Finding"

    # Warning must note dropped findings
    assert any("dropped 2" in w.lower() for w in final_state["warnings"])


# ==============================================================================
# Scenario E: Persistence Failure Rollback
# ==============================================================================
@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_and_fails(
    test_engine, mock_github_client
):
    """Verify database rollback on persistence error leaves no partial records."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "persistence-fail-rev"
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

    fake_ai = FakeGeminiClient(responses=[AIReviewResponse(findings=[])])
    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[], tool_errors=[], tools_run=[]
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

    # Simulate database error inside persist_results
    with patch(
        "app.graph.nodes.save_review_results", side_effect=RuntimeError("DB error")
    ):
        final_state = await graph.ainvoke(
            {
                "review_id": review_id,
                "pr_url": "https://github.com/owner/repo/pull/42",
                "errors": [],
                "warnings": [],
                "failed": False,
            }
        )

    assert final_state["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code == "persistence_failed"
        # Zero files or findings persisted
        files_count = db.scalar(
            select(ReviewFile).where(ReviewFile.review_id == review_id)
        )
        assert files_count is None


@pytest.mark.parametrize(
    "node_to_fail,target_patch",
    [
        ("process_diff", "app.graph.nodes.filter_and_process_files"),
        ("static_analysis", "app.graph.nodes.StaticAnalysisRunner.run"),
        ("retrieve_context", "app.graph.nodes.KeywordSymbolRetriever.retrieve"),
        ("ai_review", "app.graph.nodes.AIReviewer.review"),
        ("validate_findings", "app.graph.builder.validate_findings"),
    ],
)
@pytest.mark.asyncio
async def test_unexpected_exception_in_graph_stages_routes_to_fail_review(
    test_engine, mock_github_client, node_to_fail, target_patch
):
    """Verify unexpected exception in any processing node routes to fail_review."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = f"fail-stage-{node_to_fail}-rev"

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

    mock_analysis_runner = AsyncMock(spec=StaticAnalysisRunner)
    if node_to_fail == "static_analysis":
        mock_analysis_runner.run.side_effect = RuntimeError(
            f"Simulated crash in {node_to_fail}"
        )
    else:
        mock_analysis_runner.run.return_value = StaticAnalysisResult(
            findings=[], tool_errors=[], tools_run=[]
        )

    fake_ai = FakeGeminiClient(responses=[AIReviewResponse(findings=[])])

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_ai,
        analysis_runner=mock_analysis_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

    with patch(
        target_patch, side_effect=RuntimeError(f"Simulated crash in {node_to_fail}")
    ):
        final_state = await graph.ainvoke(
            {
                "review_id": review_id,
                "pr_url": "https://github.com/owner/repo/pull/42",
                "errors": [],
                "warnings": [],
                "failed": False,
            }
        )

    assert final_state["failed"] is True
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.FAILED
        assert rev.error_code is not None
        assert rev.completed_at is not None


@pytest.mark.asyncio
async def test_multiple_findings_identical_locations_persist_safely(
    test_engine, mock_github_client
):
    """Verify duplicate location findings generate unique fingerprints."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    review_id = "multi-find-rev-1"

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

    # Create two findings at the exact same location
    f1 = AIFinding(
        title="Remote code execution with eval",
        severity=Severity.CRITICAL,
        category=Category.SECURITY,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="eval(token)",
        explanation="Dangerous eval call.",
        suggested_fix="Do not use eval.",
        confidence=0.95,
    )
    f2 = AIFinding(
        title="Unvalidated token parameter",
        severity=Severity.HIGH,
        category=Category.SECURITY,
        file_path="app/auth.py",
        line_start=3,
        line_end=3,
        evidence="eval(token)",
        explanation="Missing validation.",
        suggested_fix="Validate token first.",
        confidence=0.88,
    )

    fake_client = AsyncMock()
    fake_client.generate_review.return_value = (
        AIReviewResponse(findings=[f1, f2], summary="Two issues on same line."),
        150,
        75,
    )

    mock_runner = AsyncMock(spec=StaticAnalysisRunner)
    mock_runner.run.return_value = StaticAnalysisResult(
        findings=[],
        tool_errors=[],
        tools_run=["bandit", "ruff"],
    )

    deps = GraphDependencies(
        session_factory=session_factory,
        github_client=mock_github_client,
        gemini_client=fake_client,
        analysis_runner=mock_runner,
        model_name="gemini-2.5-pro",
    )
    graph = build_graph(deps=deps).compile()

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
    with session_factory() as db:
        rev = db.scalar(select(Review).where(Review.id == review_id))
        assert rev.status == ReviewStatus.COMPLETED
        findings = db.scalars(
            select(Finding).where(Finding.review_id == review_id)
        ).all()
        assert len(findings) == 2
        # Verify fingerprints are unique
        fps = [f.fingerprint for f in findings]
        assert len(set(fps)) == 2
