"""Pydantic schemas package for PR Sentinel."""

from app.schemas.ai import AIFinding, AIReviewResponse, AIReviewResult
from app.schemas.analysis import StaticAnalysisResult, StaticFinding
from app.schemas.common import Page
from app.schemas.context import ContextRequest, ContextResult, ContextSnippet
from app.schemas.diff import DiffHunk, DiffLine, ProcessedFile
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot
from app.schemas.review import (
    FindingOut,
    FindingsSummary,
    FindingStatusUpdate,
    ReviewCreate,
    ReviewCreateResponse,
    ReviewFileOut,
    ReviewListItem,
    ReviewOut,
    calculate_progress_percent,
)
from app.schemas.settings import SettingsOut, SettingsUpdate

__all__ = [
    "AIFinding",
    "AIReviewResponse",
    "AIReviewResult",
    "ChangedFile",
    "ContextRequest",
    "ContextResult",
    "ContextSnippet",
    "DiffHunk",
    "DiffLine",
    "FindingOut",
    "FindingStatusUpdate",
    "FindingsSummary",
    "PRMetadata",
    "PRSnapshot",
    "Page",
    "ProcessedFile",
    "ReviewCreate",
    "ReviewCreateResponse",
    "ReviewFileOut",
    "ReviewListItem",
    "ReviewOut",
    "SettingsOut",
    "SettingsUpdate",
    "StaticAnalysisResult",
    "StaticFinding",
    "calculate_progress_percent",
]
