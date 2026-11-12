"""SQLAlchemy ORM models, enums, constraints, and indexes for PR Sentinel."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ReviewStatus(StrEnum):
    """Execution status of a pull request review."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(StrEnum):
    """Severity level of an analysis finding."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingStatus(StrEnum):
    """User triage status of a review finding."""

    OPEN = "open"
    APPROVED = "approved"
    DISMISSED = "dismissed"


class Source(StrEnum):
    """Source generator of an analysis finding."""

    STATIC = "static"
    AI = "ai"


class Category(StrEnum):
    """Taxonomy category of an analysis finding."""

    LOGIC_BUG = "logic_bug"
    SECURITY = "security"
    PERFORMANCE = "performance"
    ERROR_HANDLING = "error_handling"
    EDGE_CASE = "edge_case"
    CODE_QUALITY = "code_quality"
    TESTING = "testing"
    MISSING_TESTS = "missing_tests"


class Review(Base):
    """Pull request review record tracking lifecycle, metadata, and token stats."""

    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pr_url: Mapped[str] = mapped_column(String(500), nullable=False)
    owner: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    repo: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pr_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    pr_author: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    status: Mapped[ReviewStatus] = mapped_column(
        SAEnum(ReviewStatus, native_enum=False, create_constraint=True),
        nullable=False,
        default=ReviewStatus.QUEUED,
    )
    current_stage: Mapped[str] = mapped_column(
        String(50), nullable=False, default="queued"
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    llm_tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    dropped_findings_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    files: Mapped[list["ReviewFile"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="review",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_reviews_created_at", "created_at"),
        Index("ix_reviews_owner_repo_pr_number", "owner", "repo", "pr_number"),
        Index("ix_reviews_status", "status"),
    )


class ReviewFile(Base):
    """File touched in a reviewed pull request diff."""

    __tablename__ = "review_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="modified")
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    patch: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)
    skipped_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Relationship
    review: Mapped["Review"] = relationship(back_populates="files")

    __table_args__ = (
        Index("ix_review_files_review_id", "review_id"),
        UniqueConstraint("review_id", "path", name="uq_review_files_review_id_path"),
    )


class Finding(Base):
    """Review issue reported by deterministic static analysis or AI inspection."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[Severity] = mapped_column(
        SAEnum(Severity, native_enum=False, create_constraint=True),
        nullable=False,
    )
    category: Mapped[Category] = mapped_column(
        SAEnum(Category, native_enum=False, create_constraint=True),
        nullable=False,
    )
    source: Mapped[Source] = mapped_column(
        SAEnum(Source, native_enum=False, create_constraint=True),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[FindingStatus] = mapped_column(
        SAEnum(FindingStatus, native_enum=False, create_constraint=True),
        nullable=False,
        default=FindingStatus.OPEN,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship
    review: Mapped["Review"] = relationship(back_populates="findings")

    __table_args__ = (
        Index("ix_findings_review_id_severity", "review_id", "severity"),
        Index("ix_findings_review_id_file_path", "review_id", "file_path"),
        Index("ix_findings_review_id_status", "review_id", "status"),
        Index("ix_findings_review_id_category", "review_id", "category"),
        UniqueConstraint(
            "review_id", "fingerprint", name="uq_findings_review_id_fingerprint"
        ),
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_findings_confidence_range",
        ),
    )


class AppSettings(Base):
    """System-wide analysis and threshold configuration."""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_files: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    max_diff_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=500000)
    min_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    enabled_categories: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
    )
    gemini_model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
