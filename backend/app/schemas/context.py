"""Pydantic schemas for repository context retrieval and code snippets."""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.diff import ProcessedFile


class ContextSnippet(BaseModel):
    """An excerpt of code surrounding or relevant to pull request changes."""

    model_config = ConfigDict(from_attributes=True)

    file_path: str = Field(..., description="Repository relative file path")
    start_line: int = Field(..., ge=1, description="1-based starting line number")
    end_line: int = Field(..., ge=1, description="1-based ending line number")
    text: str = Field(..., description="Source code text of the snippet")
    reason: str = Field(
        ...,
        description=(
            "Reason snippet was extracted (e.g. changed_hunk_context, "
            "enclosing_function, import_statement, referenced_symbol)"
        ),
    )
    symbol: str | None = Field(
        default=None, description="Symbol name associated with this snippet if any"
    )
    source: str = Field(
        default="github_contents",
        description="Extraction source provider (default: github_contents)",
    )


class ContextRequest(BaseModel):
    """Specifies code files and repo coordinates for context retrieval."""

    model_config = ConfigDict(from_attributes=True)

    files: list[ProcessedFile] = Field(
        ..., description="Processed code files from the pull request"
    )
    head_sha: str = Field(..., description="Commit SHA of the HEAD version")
    owner: str = Field(..., description="Repository owner username or org")
    repo: str = Field(..., description="Repository name")


class ContextResult(BaseModel):
    """Output of context retrieval containing snippets and truncation metrics."""

    model_config = ConfigDict(from_attributes=True)

    snippets: list[ContextSnippet] = Field(
        default_factory=list, description="Extracted deduplicated code snippets"
    )
    truncated: bool = Field(
        default=False,
        description="Whether snippet extraction was truncated due to configured limits",
    )
    bytes_total: int = Field(
        default=0, ge=0, description="Total byte size of all returned snippets"
    )
    files_examined: int = Field(
        default=0, ge=0, description="Total repository files examined or fetched"
    )
