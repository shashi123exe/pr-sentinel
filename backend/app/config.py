"""Application configuration management using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Locate backend root and project root .env files safely
BACKEND_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BACKEND_DIR.parent
ENV_FILES = (
    BACKEND_DIR / ".env",
    WORKSPACE_DIR / ".env",
)


class Settings(BaseSettings):
    """PR Sentinel application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Authentication & Integrations
    GITHUB_TOKEN: SecretStr | None = Field(
        default=None,
        description="GitHub personal access token for API requests",
    )
    GEMINI_API_KEY: SecretStr | None = Field(
        default=None,
        description="Google Gemini API key, optional at startup",
    )
    GEMINI_MODEL: str | None = Field(
        default=None,
        description="Gemini model identifier without hardcoded fallback",
    )

    # Database
    DATABASE_URL: str = Field(
        default="sqlite:///./pr_sentinel.db",
        description="SQLAlchemy database connection URL",
    )

    # Access Control & Cost Protection
    APP_ACCESS_TOKEN: SecretStr | None = Field(
        default=None,
        description="Optional shared bearer token for API access protection",
    )
    MAX_REVIEWS_PER_DAY: int = Field(
        default=50,
        ge=0,
        description="Rolling 24h review limit to prevent LLM exhaustion; 0 disables",
    )

    # CORS
    CORS_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="Comma-separated allowed origins for CORS",
    )

    # Logging
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    # Frontend Static Serving (Single-service deployment mode)
    SERVE_FRONTEND: bool = Field(
        default=False,
        description="Serve compiled React SPA from static directory",
    )
    FRONTEND_DIST_DIR: str = Field(
        default="",
        description="Path to frontend production dist directory",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        """Return parsed, stripped list of allowed CORS origins."""
        if not self.CORS_ORIGINS.strip():
            return []
        return [
            origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
