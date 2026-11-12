"""Typed state definitions for LangGraph PR review orchestration."""

from typing import Any, TypedDict


class ErrorEntry(TypedDict):
    """Execution error recorded during a workflow stage."""

    code: str
    message: str
    stage: str


class TokenUsage(TypedDict):
    """Aggregate token usage for the review."""

    in_: int  # Python keyword safe alias
    out: int
    model: str


class TruncationInfo(TypedDict):
    """Truncation tracking metadata across all stages."""

    truncated: bool
    reasons: list[str]


class ReviewState(TypedDict, total=False):
    """Serializable LangGraph workflow state for PR review execution."""

    review_id: str
    pr_url: str
    parsed_pr: dict[str, Any] | None
    pr_meta: dict[str, Any] | None
    changed_files: list[dict[str, Any]]
    processed_files: list[dict[str, Any]]
    hunks: list[dict[str, Any]]
    static_findings: list[dict[str, Any]]
    tool_errors: list[str]
    context_snippets: list[dict[str, Any]]
    raw_ai_findings: list[dict[str, Any]]
    validated_findings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    token_usage: dict[str, Any]
    truncation: dict[str, Any]
    warnings: list[str]
    dropped_findings_count: int
    failed: bool
