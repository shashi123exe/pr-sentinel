"""Shared pytest fixtures for PR Sentinel backend tests."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import build_engine, get_db, seed_default_settings
from app.main import app
from app.rate_limit import limiter


@pytest.fixture
def test_db_path(tmp_path: Path) -> Path:
    """Return a unique path for a temporary SQLite test database."""
    return tmp_path / "test_pr_sentinel.db"


@pytest.fixture
def test_engine(test_db_path: Path) -> Generator[Engine, None, None]:
    """Create an isolated SQLite database engine with foreign keys and WAL enabled."""
    db_url = f"sqlite:///{test_db_path.as_posix()}"
    engine = build_engine(db_url)

    # Initialize schema tables and default settings
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with session_factory() as db:
        seed_default_settings(db)

    # Set as target for application lifespan during testing
    app.state.engine = engine
    app.state.session_factory = session_factory

    yield engine

    engine.dispose()
    if test_db_path.exists():
        try:
            test_db_path.unlink()
        except OSError:
            pass


@pytest.fixture
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    """Provide a database session connected to the temporary test database."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(test_engine: Engine) -> Generator[TestClient, None, None]:
    """Provide a FastAPI TestClient configured to use the temporary test database."""
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.engine = test_engine
    app.state.session_factory = session_factory
    limiter.reset()

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    limiter.reset()
