"""Pydantic schemas for GitHub API pull request data."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PRMetadata(BaseModel):
    """Normalized metadata for a GitHub Pull Request."""

    model_config = ConfigDict(from_attributes=True)

    title: str = Field(..., description="PR title")
    author: str = Field(..., description="PR author GitHub username")
    base_sha: str = Field(..., description="Base commit SHA")
    head_sha: str = Field(..., description="Head commit SHA")
    base_ref: str = Field(..., description="Base branch name")
    head_ref: str = Field(..., description="Head branch name")
    state: str = Field(..., description="PR state: open, closed, or merged")
    additions: int = Field(default=0, ge=0, description="Total additions")
    deletions: int = Field(default=0, ge=0, description="Total deletions")
    changed_files: int = Field(default=0, ge=0, description="Count of changed files")
    html_url: str = Field(..., description="GitHub HTML web URL for the PR")
    merged: bool = Field(default=False, description="Whether the PR is merged")

    @classmethod
    def from_github_api(cls, data: dict[str, Any]) -> "PRMetadata":
        """Construct PRMetadata safely from GitHub API GET /pulls/{number} payload."""
        user_info = data.get("user") or {}
        base_info = data.get("base") or {}
        head_info = data.get("head") or {}

        # Determine merged status
        is_merged = bool(data.get("merged", False))

        return cls(
            title=data.get("title", ""),
            author=user_info.get("login", "unknown"),
            base_sha=base_info.get("sha", ""),
            head_sha=head_info.get("sha", ""),
            base_ref=base_info.get("ref", ""),
            head_ref=head_info.get("ref", ""),
            state="merged" if is_merged else data.get("state", "open"),
            additions=data.get("additions", 0) or 0,
            deletions=data.get("deletions", 0) or 0,
            changed_files=data.get("changed_files", 0) or 0,
            html_url=data.get("html_url", ""),
            merged=is_merged,
        )


class ChangedFile(BaseModel):
    """Information regarding a modified file within a pull request."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(..., description="File path relative to repo root")
    status: str = Field(
        ...,
        description="Change status (e.g. added, modified, removed, renamed)",
    )
    additions: int = Field(default=0, ge=0, description="Added lines count")
    deletions: int = Field(default=0, ge=0, description="Deleted lines count")
    patch: str | None = Field(
        default=None, description="Unified diff patch text if available"
    )
    sha: str = Field(default="", description="Git blob SHA")
    previous_path: str | None = Field(
        default=None,
        description="Previous file path if renamed",
    )
    raw_size: int | None = Field(
        default=None,
        ge=0,
        description="Raw byte size of file if available",
    )

    @classmethod
    def from_github_api(cls, data: dict[str, Any]) -> "ChangedFile":
        """Construct ChangedFile safely from GitHub API /files item."""
        return cls(
            path=data.get("filename", ""),
            status=data.get("status", "modified"),
            additions=data.get("additions", 0) or 0,
            deletions=data.get("deletions", 0) or 0,
            patch=data.get("patch"),
            sha=data.get("sha", ""),
            previous_path=data.get("previous_filename"),
            raw_size=data.get("raw_size"),
        )


class PRSnapshot(BaseModel):
    """Complete retrieved snapshot of pull request metadata and changed files."""

    model_config = ConfigDict(from_attributes=True)

    metadata: PRMetadata
    files: list[ChangedFile]
    truncated: bool = Field(
        default=False,
        description="True if files exceeded max_files or GitHub API limit",
    )
