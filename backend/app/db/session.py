"""Database engine, session management, and startup initialization for PR Sentinel.

NOTE: Alembic migrations will be introduced in a later phase. For the MVP,
schema tables are created on application startup via Base.metadata.create_all().
"""

import logging
from collections.abc import Generator
from typing import Any

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base
from app.db.models import AppSettings, Category

logger = logging.getLogger(__name__)


def configure_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    """Enforce SQLite foreign key constraints and WAL journal mode."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


def normalize_database_url(database_url: str) -> str:
    """Normalize database connection URLs for SQLAlchemy 2.0 with Psycopg 3.

    Translates cloud provider prefixes ('postgres://', 'postgresql://') to explicit
    'postgresql+psycopg://' driver URLs while preserving SQLite and other dialects.
    """
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def validate_test_database_url(database_url: str) -> None:
    """Validate that a database URL points to a dedicated disposable test database.

    Uses SQLAlchemy make_url to strictly inspect parsed URL components independently.
    Credentials (user/password) cannot satisfy or bypass host or database checks.
    """
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(database_url)
    except Exception as exc:
        raise ValueError(f"Invalid database URL: {exc}") from exc

    driver = parsed.drivername.lower()

    # SQLite memory or test file paths are safe
    if driver.startswith("sqlite"):
        db_path = (parsed.database or "").lower()
        if ":memory:" in db_path or "test" in db_path or "disposable" in db_path:
            return
        raise ValueError(
            f"Unsafe SQLite test database path '{database_url}'. "
            "Test database URL must contain ':memory:', 'test', or 'disposable'."
        )

    # For client-server DBMS (PostgreSQL, etc.)
    # 1. Unconditionally reject production cloud hosts regardless of credentials
    host = (parsed.host or "").lower()
    prod_domains = [
        "render.com",
        "rds.amazonaws.com",
        "supabase.co",
        "neon.tech",
        "fly.dev",
        "railway.app",
        "azure.com",
        "cloudsql",
    ]
    for domain in prod_domains:
        if domain in host:
            raise ValueError(
                f"Unsafe test database URL: production cloud host '{domain}' "
                "detected in hostname. "
                "TEST_DATABASE_URL must never point to production infrastructure."
            )
    if host in ["prod", "production", "live"]:
        raise ValueError(
            f"Unsafe test database URL: production host '{host}' detected. "
            "TEST_DATABASE_URL must never point to production infrastructure."
        )

    # 2. Require dedicated disposable test database name (strictly in parsed.database)
    db_name = (parsed.database or "").lower()
    if "test" not in db_name and "disposable" not in db_name:
        raise ValueError(
            f"Unsafe test database URL: database name '{parsed.database}' must "
            "explicitly contain 'test' or 'disposable'."
        )


def build_engine(database_url: str) -> Engine:
    """Create and configure a SQLAlchemy Engine with driver-appropriate options."""
    normalized_url = normalize_database_url(database_url)
    connect_args: dict[str, Any] = {}
    engine_kwargs: dict[str, Any] = {"echo": False}

    if normalized_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        engine_kwargs["connect_args"] = connect_args
    elif "postgresql" in normalized_url:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10
        engine_kwargs["pool_timeout"] = 30.0

    new_engine = create_engine(normalized_url, **engine_kwargs)

    if normalized_url.startswith("sqlite"):
        event.listen(new_engine, "connect", configure_sqlite_pragmas)

    return new_engine


# Default application engine and session factory
settings = get_settings()
engine = build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transactional database session."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def seed_default_settings(db: Session) -> None:
    """Ensure the singleton AppSettings configuration row exists (id=1)."""
    existing = db.scalar(select(AppSettings).where(AppSettings.id == 1))
    if existing is None:
        default_settings = AppSettings(
            id=1,
            max_files=50,
            max_diff_bytes=500000,
            min_confidence=0.5,
            enabled_categories=[category.value for category in Category],
            gemini_model="",
        )
        db.add(default_settings)
        db.commit()
        logger.info("Initialized default AppSettings (id=1).")


def init_db(engine_override: Engine | None = None) -> None:
    """Create all tables and seed default configuration."""
    target_engine = engine_override or engine
    logger.info("Initializing database schema on %s...", target_engine.url)
    Base.metadata.create_all(bind=target_engine)

    # Safe SQLite migration: ensure dropped_findings_count and indexes exist
    try:
        from sqlalchemy import inspect, text

        with target_engine.connect() as conn:
            inspector = inspect(conn)
            table_names = inspector.get_table_names()
            if "reviews" in table_names:
                columns = [col["name"] for col in inspector.get_columns("reviews")]
                if "dropped_findings_count" not in columns:
                    conn.execute(
                        text(
                            "ALTER TABLE reviews ADD COLUMN dropped_findings_count "
                            "INTEGER NOT NULL DEFAULT 0"
                        )
                    )
                    conn.commit()
                    logger.info("Migrated reviews table: added dropped_findings_count.")

            if "findings" in table_names:
                # Ensure composite indexes exist on pre-existing findings table
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_findings_review_id_status "
                        "ON findings (review_id, status)"
                    )
                )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_findings_review_id_category "
                        "ON findings (review_id, category)"
                    )
                )
                conn.commit()
                logger.info("Ensured composite indexes exist on findings table.")
    except Exception as exc:
        logger.warning("Database schema inspection or migration notice: %s", exc)

    session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=target_engine
    )
    with session_factory() as db:
        seed_default_settings(db)
