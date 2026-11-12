"""Pydantic schemas for application analysis settings."""

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Category


class SettingsOut(BaseModel):
    """Current application configuration settings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    max_files: int
    max_diff_bytes: int
    min_confidence: float
    enabled_categories: list[Category]
    gemini_model: str


class SettingsUpdate(BaseModel):
    """Payload for updating application configuration settings."""

    model_config = ConfigDict(extra="forbid")

    max_files: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Maximum number of files allowed in diff (1-200)",
    )
    max_diff_bytes: int | None = Field(
        default=None,
        ge=10000,
        le=5000000,
        description="Maximum diff size in bytes (10,000 - 5,000,000)",
    )
    min_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum finding confidence threshold (0.0 - 1.0)",
    )
    enabled_categories: list[Category] | None = Field(
        default=None,
        description="List of enabled review finding categories",
    )
    gemini_model: str | None = Field(
        default=None,
        description="Gemini model identifier override (empty string uses env)",
    )
