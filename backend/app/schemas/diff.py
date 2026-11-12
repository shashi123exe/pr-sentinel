"""Pydantic schemas for unified diff parsing and file processing."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiffLine(BaseModel):
    """A single line inside a unified diff hunk."""

    model_config = ConfigDict(from_attributes=True)

    kind: Literal["add", "delete", "context"] = Field(
        ..., description="Line change type: add (+), delete (-), or context ( )"
    )
    old_no: int | None = Field(
        default=None, description="Old-file 1-based line number (None for additions)"
    )
    new_no: int | None = Field(
        default=None, description="New-file 1-based line number (None for deletions)"
    )
    text: str = Field(..., description="Actual source line text content")


class DiffHunk(BaseModel):
    """A contiguous hunk within a unified diff."""

    model_config = ConfigDict(from_attributes=True)

    old_start: int = Field(..., ge=0, description="Starting line in the old file")
    old_lines: int = Field(..., ge=0, description="Count of old-file lines in hunk")
    new_start: int = Field(..., ge=0, description="Starting line in the new file")
    new_lines: int = Field(..., ge=0, description="Count of new-file lines in hunk")
    lines: list[DiffLine] = Field(
        default_factory=list, description="Ordered lines comprising this hunk"
    )


class ProcessedFile(BaseModel):
    """A file within a pull request after diff parsing and classification."""

    model_config = ConfigDict(from_attributes=True)

    path: str = Field(..., description="Repository relative file path")
    language: str = Field(
        ...,
        description="Detected programming language (e.g. python, typescript, etc.)",
    )
    status: str = Field(
        ...,
        description="GitHub change status (added, modified, renamed, removed, etc.)",
    )
    additions: int = Field(default=0, ge=0, description="Number of added lines")
    deletions: int = Field(default=0, ge=0, description="Number of deleted lines")
    hunks: list[DiffHunk] = Field(
        default_factory=list, description="Parsed unified diff hunks"
    )
    added_line_numbers: set[int] = Field(
        default_factory=set,
        description="Set of 1-based new-file line numbers that were added/modified",
    )
    skipped_reason: str | None = Field(
        default=None,
        description="Explicit reason if this file is skipped from static analysis",
    )
    patch: str | None = Field(
        default=None, description="Original raw unified diff patch text"
    )
