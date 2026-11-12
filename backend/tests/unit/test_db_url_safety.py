"""Unit tests for database URL normalization and test database safety validation."""

import os

import pytest
from sqlalchemy import text

from app.db.session import (
    build_engine,
    normalize_database_url,
    validate_test_database_url,
)


class TestDatabaseURLNormalization:
    """Validate SQLAlchemy 2.0 and Psycopg 3 compatibility transformations."""

    def test_normalizes_legacy_postgres_prefix(self) -> None:
        """Render and legacy providers supply 'postgres://', rewritten to 'postgresql+psycopg://'."""
        legacy = "postgres://sentinel_user:secret@dpg-abc123.oregon-postgres.render.com/pr_sentinel"
        expected = "postgresql+psycopg://sentinel_user:secret@dpg-abc123.oregon-postgres.render.com/pr_sentinel"
        assert normalize_database_url(legacy) == expected

    def test_normalizes_standard_postgresql_prefix(self) -> None:
        """Standard 'postgresql://' URLs are rewritten to 'postgresql+psycopg://'."""
        standard = "postgresql://sentinel_user:secret@localhost:5432/pr_sentinel"
        expected = (
            "postgresql+psycopg://sentinel_user:secret@localhost:5432/pr_sentinel"
        )
        assert normalize_database_url(standard) == expected

    def test_preserves_explicit_postgresql_psycopg_prefix(self) -> None:
        """Explicit 'postgresql+psycopg://' URLs remain unchanged."""
        explicit = (
            "postgresql+psycopg://sentinel_user:secret@localhost:5432/pr_sentinel"
        )
        assert normalize_database_url(explicit) == explicit

    def test_preserves_sqlite_url(self) -> None:
        """SQLite URLs must remain unchanged."""
        sqlite_url = "sqlite:///./pr_sentinel.db"
        assert normalize_database_url(sqlite_url) == sqlite_url


class TestTestDatabaseSafetyValidation:
    """Validate safeguards protecting production databases from destructive tests."""

    def test_permits_safe_sqlite_urls(self) -> None:
        """In-memory or test-designated SQLite URLs are permitted."""
        validate_test_database_url("sqlite:///:memory:")
        validate_test_database_url("sqlite:///./pr_sentinel_test.db")
        validate_test_database_url("sqlite:///./disposable.db")

    def test_rejects_unsafe_sqlite_url(self) -> None:
        """SQLite URLs without explicit test/memory markers are rejected."""
        with pytest.raises(ValueError, match="Unsafe SQLite test database path"):
            validate_test_database_url("sqlite:///./pr_sentinel.db")

    def test_rejects_production_cloud_urls(self) -> None:
        """Cloud hosted production URLs are rejected, even with test db name."""
        with pytest.raises(ValueError, match="production cloud host"):
            validate_test_database_url(
                "postgresql://user:secret@dpg-xyz.oregon-postgres.render.com/pr_sentinel_test"
            )

        with pytest.raises(ValueError, match="production cloud host"):
            validate_test_database_url(
                "postgresql://admin:secret@prod-instance.rds.amazonaws.com/pr_sentinel_test"
            )

        with pytest.raises(ValueError, match="production cloud host"):
            validate_test_database_url(
                "postgresql://user:secret@db.xyz.supabase.co/test_db"
            )

    def test_rejects_non_test_client_server_db(self) -> None:
        """Local PostgreSQL without 'test' or 'disposable' in name is rejected."""
        expected_err = "must explicitly contain 'test' or 'disposable'"
        with pytest.raises(ValueError, match=expected_err):
            validate_test_database_url(
                "postgresql://user:secret@localhost:5432/pr_sentinel"
            )

    def test_rejects_credential_substring_bypass(self) -> None:
        """Credentials with 'test' cannot trick validation to allow prod db."""
        pattern = "must explicitly contain 'test' or 'disposable'"
        # Username contains 'test', but database is 'production_db'
        with pytest.raises(ValueError, match=pattern):
            validate_test_database_url(
                "postgresql://test_user:secret@localhost:5432/production_db"
            )

        # Password contains 'test', but database is 'production_db'
        with pytest.raises(ValueError, match=pattern):
            validate_test_database_url(
                "postgresql://prod_admin:my_test_password@localhost:5432/production_db"
            )

        # Both username and password contain 'disposable', database is 'live_orders'
        with pytest.raises(ValueError, match=pattern):
            validate_test_database_url(
                "postgresql://disposable:disposable_pass@127.0.0.1:5432/live_orders"
            )

    def test_permits_valid_disposable_test_db(self) -> None:
        """PostgreSQL URLs clearly targeting a disposable test database are allowed."""
        validate_test_database_url(
            "postgresql://user:secret@localhost:5432/pr_sentinel_test"
        )
        validate_test_database_url(
            "postgresql://user:secret@127.0.0.1:5432/disposable_ci_db"
        )


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.postgres
@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set; skipping PostgreSQL integration test",
)
def test_postgresql_connection_live() -> None:
    """Verify live PostgreSQL connectivity, driver binding, and query execution."""
    validated_url = validate_test_database_url(TEST_DATABASE_URL)  # type: ignore[arg-type]
    normalized_url = normalize_database_url(validated_url)
    engine = build_engine(normalized_url)
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            assert result == 1
    finally:
        engine.dispose()
