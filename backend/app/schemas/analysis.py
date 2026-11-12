"""Pydantic schemas for static analysis findings and execution results."""

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Category, Severity


class StaticFinding(BaseModel):
    """An issue detected by an automated static analysis tool (Ruff, Bandit, ESLint)."""

    model_config = ConfigDict(from_attributes=True)

    file_path: str = Field(..., description="Repository relative file path")
    line_start: int = Field(..., ge=1, description="1-based starting line number")
    line_end: int = Field(..., ge=1, description="1-based ending line number")
    title: str = Field(..., description="Concise finding headline or rule description")
    severity: Severity = Field(
        ..., description="Severity level: critical, high, medium, low"
    )
    category: Category = Field(
        ...,
        description=(
            "Taxonomy category (security, performance, logic_bug, code_quality, etc.)"
        ),
    )
    tool_name: str = Field(..., description="Tool name: ruff, bandit, or eslint")
    rule_id: str = Field(..., description="Linter or security check rule code")
    evidence: str = Field(
        ..., description="Exact verbatim source line text from HEAD commit"
    )
    explanation: str = Field(..., description="Detailed description of the violation")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for this finding (0.0 to 1.0)",
    )
    suggested_fix: str | None = Field(
        default=None, description="Optional suggested code replacement or advice"
    )


class StaticAnalysisResult(BaseModel):
    """Aggregated output from running all configured static analysis tools."""

    model_config = ConfigDict(from_attributes=True)

    findings: list[StaticFinding] = Field(
        default_factory=list, description="Validated findings on added/modified lines"
    )
    tool_errors: list[str] = Field(
        default_factory=list,
        description="Error messages recorded if any tool crashed or failed",
    )
    tools_run: list[str] = Field(
        default_factory=list,
        description="List of tools that were executed (e.g. ruff, bandit, eslint)",
    )
