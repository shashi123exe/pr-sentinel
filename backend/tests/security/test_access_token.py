"""Security tests for the shared bearer access token gate."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import AppSettings, Category
from app.db.session import build_engine, get_db, init_db, sessionmaker
from app.main import create_app

SHARED_SECRET = "sentinel-super-secret-token-2026"


@pytest.fixture
def auth_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Test client configured with APP_ACCESS_TOKEN enabled."""
    db_file = tmp_path / "auth_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    test_settings = Settings(
        DATABASE_URL=db_url,
        APP_ACCESS_TOKEN=SecretStr(SHARED_SECRET),
        MAX_REVIEWS_PER_DAY=50,
        LOG_LEVEL="WARNING",
    )

    test_engine = build_engine(db_url)
    init_db(engine_override=test_engine)
    test_session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    with test_session_factory() as db:
        if db.query(AppSettings).filter_by(id=1).first() is None:
            db.add(
                AppSettings(
                    id=1,
                    max_files=50,
                    max_diff_bytes=500000,
                    min_confidence=0.5,
                    enabled_categories=[c.value for c in Category],
                    gemini_model="",
                )
            )
            db.commit()

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    application = create_app(settings_override=test_settings)
    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: test_settings
    application.state.engine = test_engine
    application.state.session_factory = test_session_factory

    with TestClient(application) as client:
        yield client

    test_engine.dispose()


@pytest.fixture
def unauthenticated_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    """Test client configured with APP_ACCESS_TOKEN disabled (None)."""
    db_file = tmp_path / "unauth_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    test_settings = Settings(
        DATABASE_URL=db_url,
        APP_ACCESS_TOKEN=None,
        MAX_REVIEWS_PER_DAY=50,
        LOG_LEVEL="WARNING",
    )

    test_engine = build_engine(db_url)
    init_db(engine_override=test_engine)
    test_session_factory = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    with test_session_factory() as db:
        if db.query(AppSettings).filter_by(id=1).first() is None:
            db.add(
                AppSettings(
                    id=1,
                    max_files=50,
                    max_diff_bytes=500000,
                    min_confidence=0.5,
                    enabled_categories=[c.value for c in Category],
                    gemini_model="",
                )
            )
            db.commit()

    def override_get_db() -> Generator[Session, None, None]:
        db = test_session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    application = create_app(settings_override=test_settings)
    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: test_settings
    application.state.engine = test_engine
    application.state.session_factory = test_session_factory

    with TestClient(application) as client:
        yield client

    test_engine.dispose()


class TestAccessTokenGating:
    """Validate shared access token enforcement, bypasses, and security."""

    def test_gate_disabled_permits_unauthenticated_requests(
        self, unauthenticated_client: TestClient
    ) -> None:
        """When APP_ACCESS_TOKEN is unset, protected endpoints are accessible."""
        response = unauthenticated_client.get("/api/settings")
        assert response.status_code == 200
        assert "max_files" in response.json()

    def test_health_check_bypasses_access_token_gate(
        self, auth_client: TestClient
    ) -> None:
        """Health check endpoint must remain public without an Authorization header."""
        response = auth_client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": "ok"}

    def test_missing_authorization_header_rejected(
        self, auth_client: TestClient
    ) -> None:
        """Requests without Authorization header receive HTTP 401."""
        response = auth_client.get("/api/settings")
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "unauthorized"
        assert response.headers.get("www-authenticate") == "Bearer"

    def test_malformed_authorization_header_rejected(
        self, auth_client: TestClient
    ) -> None:
        """Malformed authorization schemes receive HTTP 401."""
        # Not Bearer scheme
        res1 = auth_client.get(
            "/api/settings", headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert res1.status_code == 401
        assert res1.json()["error"]["code"] == "unauthorized"

        # Missing token part
        res2 = auth_client.get("/api/settings", headers={"Authorization": "Bearer"})
        assert res2.status_code == 401

        # Extra whitespace tokens
        res3 = auth_client.get(
            "/api/settings", headers={"Authorization": "Bearer token extra"}
        )
        assert res3.status_code == 401

    def test_invalid_token_rejected(self, auth_client: TestClient) -> None:
        """Incorrect token strings receive HTTP 401."""
        response = auth_client.get(
            "/api/settings", headers={"Authorization": "Bearer wrong-token-xyz"}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "unauthorized"
        assert "Invalid access token" in data["error"]["message"]
        # Error envelope must never leak the secret
        assert SHARED_SECRET not in response.text

    def test_valid_token_accepted(self, auth_client: TestClient) -> None:
        """Correct token returns HTTP 200 on protected endpoint."""
        response = auth_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {SHARED_SECRET}"},
        )
        assert response.status_code == 200
        assert "max_files" in response.json()

    def test_valid_x_access_token_header_accepted(
        self, auth_client: TestClient
    ) -> None:
        """Valid token supplied via X-Access-Token header returns HTTP 200."""
        response = auth_client.get(
            "/api/settings",
            headers={"X-Access-Token": SHARED_SECRET},
        )
        assert response.status_code == 200
        assert "max_files" in response.json()

    def test_invalid_x_access_token_header_rejected(
        self, auth_client: TestClient
    ) -> None:
        """Invalid token supplied via X-Access-Token header returns HTTP 401."""
        response = auth_client.get(
            "/api/settings",
            headers={"X-Access-Token": "invalid-passphrase"},
        )
        assert response.status_code == 401
        data = response.json()
        assert data["error"]["code"] == "unauthorized"
        assert "Invalid access token" in data["error"]["message"]

    def test_constant_time_comparison_used(self, auth_client: TestClient) -> None:
        """Verify secrets.compare_digest is utilized for timing-attack resistance."""
        with patch("secrets.compare_digest", return_value=True) as mock_compare:
            response = auth_client.get(
                "/api/settings",
                headers={"Authorization": "Bearer mocked-token"},
            )
            assert response.status_code == 200
            assert mock_compare.called
