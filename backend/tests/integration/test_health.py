"""Integration tests for the /api/health endpoint."""

from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app


def test_health_check_success(client: TestClient):
    """Verify health endpoint returns 200 and database ok."""
    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data == {"status": "ok", "database": "ok"}
    assert "X-Request-ID" in response.headers
    assert response.headers["X-Request-ID"] != ""


def test_health_check_database_failure():
    """Verify health endpoint returns 503 error envelope when database fails."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = OperationalError("SELECT 1", {}, Exception("DB down"))

    def failing_db():
        yield mock_db

    app.dependency_overrides[get_db] = failing_db
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/api/health")
            assert response.status_code == 503
            data = response.json()
            assert "error" in data
            assert data["error"]["code"] == "database_unavailable"
            assert "request_id" in data["error"]
            assert data["error"]["request_id"] != "-"
    finally:
        app.dependency_overrides.clear()
