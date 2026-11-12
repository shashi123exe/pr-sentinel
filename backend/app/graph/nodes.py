"""LangGraph workflow node implementations for PR review execution."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gemini_client import GeminiClient, GeminiClientProtocol
from app.ai.reviewer import AIReviewer
from app.analysis.runner import StaticAnalysisRunner
from app.config import get_settings
from app.context.keyword_retriever import KeywordSymbolRetriever
from app.db.models import (
    AppSettings,
    Review,
    ReviewStatus,
)
from app.db.session import SessionLocal
from app.errors import AppError
from app.graph.state import ReviewState
from app.schemas.analysis import StaticFinding
from app.schemas.context import ContextRequest, ContextSnippet
from app.schemas.diff import ProcessedFile
from app.schemas.github import ChangedFile, PRMetadata
from app.services.dedupe import deduplicate_findings
from app.services.file_filter import filter_and_process_files
from app.services.github_client import GitHubClient
from app.services.persistence import save_review_results
from app.services.url_parser import parse_pr_url
from app.services.validation import FindingValidator

logger = logging.getLogger(__name__)


class GraphDependencies:
    """Dependency container for injecting services into LangGraph review nodes."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
        github_client: GitHubClient | None = None,
        gemini_client: GeminiClientProtocol | None = None,
        analysis_runner: StaticAnalysisRunner | None = None,
        model_name: str | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        self.session_factory = session_factory
        self.github_client = github_client
        self.gemini_client = gemini_client
        self.analysis_runner = analysis_runner
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._managed_github_client: GitHubClient | None = None

    def get_github_client(self) -> GitHubClient:
        """Return configured or injected GitHub API client."""
        if self.github_client is not None:
            return self.github_client
        if self._managed_github_client is None:
            token = get_settings().GITHUB_TOKEN
            self._managed_github_client = GitHubClient(token=token)
        return self._managed_github_client

    async def aclose(self) -> None:
        """Close any managed HTTP or API clients created by dependencies."""
        if self._managed_github_client is not None:
            await self._managed_github_client.aclose()
            self._managed_github_client = None

    def get_analysis_runner(self) -> StaticAnalysisRunner:
        """Return configured or injected static analysis runner."""
        if self.analysis_runner is not None:
            return self.analysis_runner
        return StaticAnalysisRunner()

    def get_reviewer(self, app_settings: AppSettings | None = None) -> AIReviewer:
        """Return configured or injected AI reviewer."""
        client = self.gemini_client or GeminiClient()
        settings = app_settings
        if self.model_name:
            if settings is None:
                settings = AppSettings(
                    gemini_model=self.model_name, enabled_categories=[]
                )
            elif not settings.gemini_model:
                settings = AppSettings(
                    gemini_model=self.model_name,
                    max_files=settings.max_files,
                    max_diff_bytes=settings.max_diff_bytes,
                    min_confidence=settings.min_confidence,
                    enabled_categories=settings.enabled_categories,
                )
        return AIReviewer(client=client, settings=settings)


def update_review_stage(
    session_factory: Callable[[], Session],
    review_id: str,
    stage: str,
    status: ReviewStatus = ReviewStatus.RUNNING,
) -> None:
    """Update review stage and status using a short-lived database session."""
    with session_factory() as db:
        review = db.scalar(select(Review).where(Review.id == review_id))
        if review:
            review.current_stage = stage
            review.status = status
            db.commit()


# ==============================================================================
# Node A: fetch_pr
# ==============================================================================
async def fetch_pr(state: ReviewState, deps: GraphDependencies) -> dict[str, Any]:
    """Fetch pull request metadata and changed files snapshot from GitHub."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "fetch_pr")

    try:
        parsed = parse_pr_url(state["pr_url"])

        with deps.session_factory() as db:
            app_settings = db.scalar(select(AppSettings).where(AppSettings.id == 1))

        gh_client = deps.get_github_client()
        snapshot = await gh_client.fetch_pr_snapshot(parsed, app_settings)

        # Update database review record with initial metadata
        with deps.session_factory() as db:
            review = db.scalar(select(Review).where(Review.id == review_id))
            if review:
                review.pr_title = snapshot.metadata.title
                review.pr_author = snapshot.metadata.author
                review.base_sha = snapshot.metadata.base_sha
                review.head_sha = snapshot.metadata.head_sha
                review.files_changed = snapshot.metadata.changed_files
                review.additions = snapshot.metadata.additions
                review.deletions = snapshot.metadata.deletions
                review.truncated = snapshot.truncated
                db.commit()

        reasons = ["max_files_exceeded"] if snapshot.truncated else []
        return {
            "parsed_pr": {
                "owner": parsed.owner,
                "repo": parsed.repo,
                "number": parsed.number,
                "canonical_url": parsed.canonical_url,
            },
            "pr_meta": snapshot.metadata.model_dump(),
            "changed_files": [f.model_dump() for f in snapshot.files],
            "truncation": {"truncated": snapshot.truncated, "reasons": reasons},
            "failed": False,
        }

    except AppError as exc:
        logger.error(
            "fetch_pr node failed for review %s [%s]: %s",
            review_id,
            exc.code,
            exc.message,
        )
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [{"code": exc.code, "message": exc.message, "stage": "fetch_pr"}],
        }
    except Exception as exc:
        logger.error("Unexpected error in fetch_pr for %s: %s", review_id, exc)
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "fetch_pr_failed",
                    "message": "Failed to fetch pull request from GitHub.",
                    "stage": "fetch_pr",
                }
            ],
        }


# ==============================================================================
# Node B: process_diff
# ==============================================================================
async def process_diff(state: ReviewState, deps: GraphDependencies) -> dict[str, Any]:
    """Parse unified diff patches, filter unsupported files, and enforce caps."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "process_diff")

    try:
        raw_files = state.get("changed_files", [])
        changed_files = [ChangedFile.model_validate(f) for f in raw_files]

        with deps.session_factory() as db:
            app_settings = db.scalar(select(AppSettings).where(AppSettings.id == 1))

        all_files, metrics = filter_and_process_files(changed_files, app_settings)

        # Collect hunks from eligible files
        hunks = [
            hunk.model_dump()
            for f in all_files
            if f.skipped_reason is None
            for hunk in f.hunks
        ]

        prev_trunc = state.get("truncation", {"truncated": False, "reasons": []})
        reasons = list(prev_trunc.get("reasons", []))
        if metrics.reason and metrics.reason not in reasons:
            reasons.append(metrics.reason)
        is_trunc = prev_trunc.get("truncated", False) or metrics.truncated

        return {
            "processed_files": [f.model_dump() for f in all_files],
            "hunks": hunks,
            "truncation": {"truncated": is_trunc, "reasons": reasons},
            "failed": False,
        }

    except AppError as exc:
        logger.error(
            "process_diff failed for review %s [%s]: %s",
            review_id,
            exc.code,
            exc.message,
        )
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [{"code": exc.code, "message": exc.message, "stage": "process_diff"}],
        }
    except Exception as exc:
        logger.error("Unexpected error in process_diff for %s: %s", review_id, exc)
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "process_diff_failed",
                    "message": "Failed to parse and process diffs.",
                    "stage": "process_diff",
                }
            ],
        }


# ==============================================================================
# Node C: static_analysis
# ==============================================================================
async def static_analysis(
    state: ReviewState, deps: GraphDependencies
) -> dict[str, Any]:
    """Execute isolated static analysis (Ruff, Bandit, ESLint) on modified code."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "static_analysis")

    try:
        raw_proc_files = state.get("processed_files", [])
        proc_files = [ProcessedFile.model_validate(f) for f in raw_proc_files]
        eligible = [f for f in proc_files if f.skipped_reason is None]

        if not eligible:
            logger.info("No eligible files for static analysis in %s", review_id)
            return {
                "static_findings": [],
                "tool_errors": [],
                "failed": False,
            }

        parsed_pr = state.get("parsed_pr") or {}
        pr_meta = state.get("pr_meta") or {}
        owner = parsed_pr.get("owner", "")
        repo = parsed_pr.get("repo", "")
        head_sha = pr_meta.get("head_sha", "")

        gh_client = deps.get_github_client()
        file_contents: dict[str, str] = {}
        for pf in eligible:
            content = await gh_client.get_file_content(
                owner=owner,
                repo=repo,
                path=pf.path,
                ref=head_sha,
            )
            if content is not None:
                file_contents[pf.path] = content

        runner = deps.get_analysis_runner()
        result = await runner.run(eligible, file_contents)

        warnings = list(state.get("warnings", []))
        for err in result.tool_errors:
            warnings.append(f"Static tool warning: {err}")

        return {
            "static_findings": [f.model_dump() for f in result.findings],
            "tool_errors": result.tool_errors,
            "warnings": warnings,
            "failed": False,
        }

    except AppError as exc:
        logger.error(
            "static_analysis failed for %s [%s]: %s",
            review_id,
            exc.code,
            exc.message,
        )
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [{"code": exc.code, "message": exc.message, "stage": "static_analysis"}],
        }
    except Exception as exc:
        logger.error("Unexpected error in static_analysis for %s: %s", review_id, exc)
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "static_analysis_failed",
                    "message": "Static analysis failed to execute.",
                    "stage": "static_analysis",
                }
            ],
        }


# ==============================================================================
# Node D: retrieve_context
# ==============================================================================
async def retrieve_context(
    state: ReviewState, deps: GraphDependencies
) -> dict[str, Any]:
    """Retrieve surrounding and referenced code context via GitHub Contents API."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "retrieve_context")

    try:
        raw_proc_files = state.get("processed_files", [])
        proc_files = [ProcessedFile.model_validate(f) for f in raw_proc_files]

        parsed_pr = state.get("parsed_pr") or {}
        pr_meta = state.get("pr_meta") or {}
        owner = parsed_pr.get("owner", "")
        repo = parsed_pr.get("repo", "")
        head_sha = pr_meta.get("head_sha", "")

        gh_client = deps.get_github_client()
        retriever = KeywordSymbolRetriever(github_client=gh_client)

        context_req = ContextRequest(
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            files=proc_files,
        )
        context_result = await retriever.retrieve(context_req)

        prev_trunc = state.get("truncation", {"truncated": False, "reasons": []})
        reasons = list(prev_trunc.get("reasons", []))
        if context_result.truncated and "context_truncated" not in reasons:
            reasons.append("context_truncated")
        is_trunc = prev_trunc.get("truncated", False) or context_result.truncated

        return {
            "context_snippets": [s.model_dump() for s in context_result.snippets],
            "truncation": {"truncated": is_trunc, "reasons": reasons},
            "failed": False,
        }

    except AppError as exc:
        logger.error(
            "retrieve_context failed for %s [%s]: %s",
            review_id,
            exc.code,
            exc.message,
        )
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": exc.code,
                    "message": exc.message,
                    "stage": "retrieve_context",
                }
            ],
        }
    except Exception as exc:
        logger.error("Unexpected error in retrieve_context for %s: %s", review_id, exc)
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "context_retrieval_failed",
                    "message": "Failed to retrieve code context.",
                    "stage": "retrieve_context",
                }
            ],
        }


# ==============================================================================
# Node E: ai_review
# ==============================================================================
async def ai_review(state: ReviewState, deps: GraphDependencies) -> dict[str, Any]:
    """Execute Gemini AI review with token budgeting and AI-unavailable policy."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "ai_review")

    raw_proc = state.get("processed_files", [])
    proc_files = [ProcessedFile.model_validate(f) for f in raw_proc]
    eligible = [f for f in proc_files if f.skipped_reason is None]

    raw_snips = state.get("context_snippets", [])
    snippets = [ContextSnippet.model_validate(s) for s in raw_snips]

    raw_static = state.get("static_findings", [])
    static_finds = [StaticFinding.model_validate(sf) for sf in raw_static]

    raw_meta = state.get("pr_meta") or {}
    pr_meta = PRMetadata.model_validate(raw_meta)

    with deps.session_factory() as db:
        app_settings = db.scalar(select(AppSettings).where(AppSettings.id == 1))

    try:
        reviewer = deps.get_reviewer(app_settings)
        ai_result = await reviewer.review(
            metadata=pr_meta,
            files=eligible,
            context_snippets=snippets,
            static_findings=static_finds,
        )

        # Check if AI batches completely failed (e.g. key missing or service down)
        if ai_result.errors and not ai_result.findings:
            has_static = len(state.get("static_findings", [])) > 0
            first_err = ai_result.errors[0]
            err_code = "ai_unavailable"
            err_msg = first_err
            if "[" in first_err and "]" in first_err:
                err_code = first_err.split("[")[1].split("]")[0]
                if ":" in first_err:
                    err_msg = first_err.split("]:", 1)[1].strip()

            if has_static:
                # Case 1: AI unavailable and static findings exist -> continue workflow
                warnings = list(state.get("warnings", []))
                warnings.append("AI review unavailable")
                return {
                    "failed": False,
                    "raw_ai_findings": [],
                    "token_usage": {
                        "in": ai_result.tokens_in,
                        "out": ai_result.tokens_out,
                        "model": ai_result.model,
                    },
                    "warnings": warnings,
                    "errors": state.get("errors", [])
                    + [
                        {
                            "code": err_code,
                            "message": "AI review unavailable",
                            "stage": "ai_review",
                        }
                    ],
                }
            else:
                # Case 2: AI unavailable and NO static findings -> fail review
                return {
                    "failed": True,
                    "raw_ai_findings": [],
                    "token_usage": {
                        "in": ai_result.tokens_in,
                        "out": ai_result.tokens_out,
                        "model": ai_result.model,
                    },
                    "errors": state.get("errors", [])
                    + [
                        {
                            "code": err_code,
                            "message": err_msg,
                            "stage": "ai_review",
                        }
                    ],
                }

        prev_trunc = state.get("truncation", {"truncated": False, "reasons": []})
        reasons = list(prev_trunc.get("reasons", []))
        if ai_result.truncated and "ai_budget_truncated" not in reasons:
            reasons.append("ai_budget_truncated")
        is_trunc = prev_trunc.get("truncated", False) or ai_result.truncated

        warnings = list(state.get("warnings", []))
        for err in ai_result.errors:
            warnings.append(f"AI review warning: {err}")

        return {
            "raw_ai_findings": [f.model_dump() for f in ai_result.findings],
            "token_usage": {
                "in": ai_result.tokens_in,
                "out": ai_result.tokens_out,
                "model": ai_result.model,
            },
            "truncation": {"truncated": is_trunc, "reasons": reasons},
            "warnings": warnings,
            "failed": False,
        }

    except Exception as exc:
        has_static = len(state.get("static_findings", [])) > 0
        err_code = (
            exc.code
            if isinstance(exc, AppError)
            else "ai_not_configured"
            if "not configured" in str(exc).lower()
            else "ai_unavailable"
        )
        err_msg = (
            exc.message
            if isinstance(exc, AppError)
            else "AI review service is unavailable."
        )

        logger.warning(
            "AI review encountered error for %s [%s]: %s (has_static=%s)",
            review_id,
            err_code,
            err_msg,
            has_static,
        )

        if has_static:
            # Case 1: AI unavailable and static findings exist -> continue workflow
            return {
                "failed": False,
                "raw_ai_findings": [],
                "token_usage": {"in": 0, "out": 0, "model": ""},
                "warnings": state.get("warnings", []) + ["AI review unavailable"],
                "errors": state.get("errors", [])
                + [
                    {
                        "code": err_code,
                        "message": "AI review unavailable",
                        "stage": "ai_review",
                    }
                ],
            }
        else:
            # Case 2: AI unavailable and no static findings exist -> fail review
            return {
                "failed": True,
                "raw_ai_findings": [],
                "token_usage": {"in": 0, "out": 0, "model": ""},
                "errors": state.get("errors", [])
                + [{"code": err_code, "message": err_msg, "stage": "ai_review"}],
            }


# ==============================================================================
# Node F: validate_findings
# ==============================================================================
async def validate_findings(
    state: ReviewState, deps: GraphDependencies
) -> dict[str, Any]:
    """Validate AI/static findings, verify evidence, filter confidence, and dedupe."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "validate_findings")

    try:
        raw_proc = state.get("processed_files", [])
        proc_files = [ProcessedFile.model_validate(f) for f in raw_proc]

        raw_snips = state.get("context_snippets", [])
        snippets = [ContextSnippet.model_validate(s) for s in raw_snips]

        with deps.session_factory() as db:
            app_settings = db.scalar(select(AppSettings).where(AppSettings.id == 1))

        validator = FindingValidator(
            files=proc_files,
            context_snippets=snippets,
            app_settings=app_settings,
        )

        validation_result = validator.validate_all(
            raw_ai_findings=state.get("raw_ai_findings", []),
            raw_static_findings=state.get("static_findings", []),
        )

        # Deduplicate validated findings (merging static+AI and collapsing identicals)
        deduped = deduplicate_findings(validation_result.valid_findings)

        warnings = list(state.get("warnings", []))
        if validation_result.dropped_count > 0:
            reasons_summary = ", ".join(
                f"{k}: {v}" for k, v in sorted(validation_result.drop_reasons.items())
            )
            warnings.append(
                f"Validation accepted {len(deduped)} finding(s), dropped "
                f"{validation_result.dropped_count} finding(s) ({reasons_summary})"
            )

        return {
            "validated_findings": deduped,
            "dropped_findings_count": validation_result.dropped_count,
            "warnings": warnings,
            "failed": False,
        }

    except Exception as exc:
        logger.error("Validation node failed for %s: %s", review_id, exc)
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "validation_failed",
                    "message": "Failed to validate review findings.",
                    "stage": "validate_findings",
                }
            ],
        }


# ==============================================================================
# Node G: persist_results
# ==============================================================================
async def persist_results(
    state: ReviewState, deps: GraphDependencies
) -> dict[str, Any]:
    """Persist processed files, findings, and review status in one transaction."""
    review_id = state["review_id"]
    update_review_stage(deps.session_factory, review_id, "persist_results")

    try:
        with deps.session_factory() as db:
            save_review_results(
                session=db,
                review_id=review_id,
                files=state.get("processed_files", []),
                findings=state.get("validated_findings", []),
                usage=state.get("token_usage", {}),
                truncation=state.get("truncation", {}),
                warnings=state.get("warnings", []),
                dropped_findings_count=state.get("dropped_findings_count", 0),
            )
            return {"failed": False}

    except Exception as exc:
        logger.error(
            "Transaction rollback: persistence failed for review %s: %s",
            review_id,
            exc,
        )
        return {
            "failed": True,
            "errors": state.get("errors", [])
            + [
                {
                    "code": "persistence_failed",
                    "message": "Failed to persist review results to database.",
                    "stage": "persist_results",
                }
            ],
        }


# ==============================================================================
# Node H: fail_review
# ==============================================================================
async def fail_review(state: ReviewState, deps: GraphDependencies) -> dict[str, Any]:
    """Dedicated failure node recording safe error codes and marking review failed."""
    review_id = state["review_id"]

    try:
        with deps.session_factory() as db:
            review = db.scalar(select(Review).where(Review.id == review_id))
            if review:
                review.status = ReviewStatus.FAILED
                review.completed_at = datetime.now(UTC)

                errors = state.get("errors", [])
                if errors:
                    last_err = errors[-1]
                    review.error_code = last_err.get("code", "internal_error")
                    review.error_message = last_err.get(
                        "message", "Review execution failed."
                    )
                elif not review.error_code:
                    review.error_code = "execution_failed"
                    review.error_message = "Review execution failed."

                db.commit()
                logger.info(
                    "Marked review [%s] as failed (code: %s, stage: %s)",
                    review_id,
                    review.error_code,
                    review.current_stage,
                )
    except Exception as exc:
        logger.error("Failed to mark review [%s] as failed: %s", review_id, exc)

    return {"failed": True}
