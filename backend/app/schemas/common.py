"""Common generic schemas for PR Sentinel APIs."""

from pydantic import BaseModel, ConfigDict, Field


class Page[T](BaseModel):
    """Standardized paginated list envelope."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T] = Field(..., description="List of items for the requested page")
    total: int = Field(..., ge=0, description="Total count of items across all pages")
    page: int = Field(..., ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(..., ge=1, description="Number of items per page")
