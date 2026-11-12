"""Pydantic schemas for pull request reviews."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import Category, FindingStatus, ReviewStatus, Severity, Source

STAGE_PROGRESS_MAP: dict[str, int] = {
    "queued": 0,
    "fetch_pr": 10,
    "process_diff": 25,
    "static_analysis": 45,
    "retrieve_context": 60,
    "ai_review": 80,
    "validate_findings": 90,
    "persist_results": 95,
    "completed": 100,
}


def calculate_progress_percent(status: ReviewStatus | str, current_stage: str) -> int:
    """Calculate 0-100 progress percent based on status and execution stage."""
    status_str = status.value if isinstance(status, ReviewStatus) else str(status)
    if status_str == ReviewStatus.COMPLETED.value:
        return 100
    if status_str == ReviewStatus.QUEUED.value:
        return 0
    return STAGE_PROGRESS_MAP.get(current_stage, 0)


class ReviewCreate(BaseModel):
    """Payload for submitting a new GitHub PR review request."""

    pr_url: str = Field(
        ...,
        description="GitHub Pull Request URL (e.g. https://github.com/owner/repo/pull/1)",
    )

    @field_validator("pr_url")
    @classmethod
    def validate_non_empty_url(cls, v: str) -> str:
        """Validate that the PR URL string is non-empty for Phase 2."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("pr_url must be a non-empty string")
        return stripped


class ReviewCreateResponse(BaseModel):
    """Immediate response confirming a review has been queued."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: ReviewStatus = ReviewStatus.QUEUED


class FindingsSummary(BaseModel):
    """Aggregate finding count broken down by severity."""

    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0


class ReviewListItem(BaseModel):
    """Lightweight summary of a review for list endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    pr_url: str
    owner: str
    repo: str
    pr_number: int
    pr_title: str
    pr_author: str
    status: ReviewStatus
    current_stage: str
    files_changed: int
    additions: int
    deletions: int
    findings_summary: FindingsSummary = Field(default_factory=FindingsSummary)
    created_at: datetime
    completed_at: datetime | None


class ReviewOut(BaseModel):
    """Detailed review entity representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    pr_url: str
    owner: str
    repo: str
    pr_number: int
    pr_title: str
    pr_author: str
    base_sha: str
    head_sha: str
    status: ReviewStatus
    current_stage: str
    progress_percent: int = 0
    error_code: str | None = None
    error_message: str | None = None
    files_changed: int
    additions: int
    deletions: int
    truncated: bool
    llm_model: str
    llm_tokens_in: int
    llm_tokens_out: int
    warnings: list[str] = Field(default_factory=list)
    findings_summary: FindingsSummary = Field(default_factory=FindingsSummary)
    dropped_findings_count: int = 0
    created_at: datetime
    completed_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def compute_progress_and_warnings(cls, data: Any) -> Any:
        """Compute progress percentage and normalize warnings from dict or ORM."""
        if isinstance(data, dict):
            status = data.get("status", ReviewStatus.QUEUED)
            stage = data.get("current_stage", "queued")
            if "progress_percent" not in data or data["progress_percent"] == 0:
                data["progress_percent"] = calculate_progress_percent(status, stage)
            raw_w = data.get("warnings") or []
            data["warnings"] = [str(item) for item in raw_w]
            if "findings_summary" not in data or data["findings_summary"] is None:
                data["findings_summary"] = FindingsSummary()
            return data

        if hasattr(data, "status") and hasattr(data, "current_stage"):
            status = data.status
            stage = data.current_stage
            progress = calculate_progress_percent(status, stage)
            raw_warnings = getattr(data, "warnings", []) or []
            f_summary = getattr(data, "findings_summary", None) or FindingsSummary()
            dropped_cnt = getattr(data, "dropped_findings_count", 0) or 0
            return {
                "id": data.id,
                "pr_url": data.pr_url,
                "owner": data.owner,
                "repo": data.repo,
                "pr_number": data.pr_number,
                "pr_title": data.pr_title,
                "pr_author": data.pr_author,
                "base_sha": data.base_sha,
                "head_sha": data.head_sha,
                "status": data.status,
                "current_stage": data.current_stage,
                "progress_percent": progress,
                "error_code": data.error_code,
                "error_message": data.error_message,
                "files_changed": data.files_changed,
                "additions": data.additions,
                "deletions": data.deletions,
                "truncated": data.truncated,
                "llm_model": data.llm_model,
                "llm_tokens_in": data.llm_tokens_in,
                "llm_tokens_out": data.llm_tokens_out,
                "warnings": [str(w) for w in raw_warnings],
                "findings_summary": f_summary,
                "dropped_findings_count": dropped_cnt,
                "created_at": data.created_at,
                "completed_at": data.completed_at,
            }
        return data


class FindingOut(BaseModel):
    """Review issue finding output representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: str
    file_path: str
    line_start: int
    line_end: int
    title: str
    severity: Severity
    category: Category
    source: Source
    tool_name: str
    rule_id: str
    evidence: str
    explanation: str
    suggested_fix: str | None = None
    confidence: float
    fingerprint: str
    status: FindingStatus
    created_at: datetime
    updated_at: datetime | None = None


class FindingStatusUpdate(BaseModel):
    """Payload for updating finding triage status."""

    model_config = ConfigDict(extra="forbid")

    status: FindingStatus


class ReviewFileOut(BaseModel):
    """Touched PR file output representation."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: str
    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None
    language: str | None = None
    skipped_reason: str | None = None
    findings_count: int = 0
