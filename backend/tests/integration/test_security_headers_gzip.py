"""Integration tests for production security headers and GZip response compression."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_security_headers_present_on_api_responses(client: TestClient) -> None:
    """Verify standard production security headers are set on API responses."""
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_security_headers_present_on_error_responses(client: TestClient) -> None:
    """Verify security headers are preserved on error responses."""
    response = client.get("/api/nonexistent-endpoint-test")

    assert response.status_code == 404
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_gzip_compression_skipped_for_small_responses(client: TestClient) -> None:
    """Verify small responses below 1000 bytes are not compressed."""
    response = client.get("/api/health", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    # Payload is small (~30 bytes), so GZipMiddleware does not compress it
    assert "content-encoding" not in response.headers


def test_gzip_compression_applied_to_large_responses(tmp_path) -> None:
    """Verify responses >= 1000 bytes are compressed with GZip."""
    # Create a test app with SERVE_FRONTEND enabled pointing to a mock dist dir
    dist_dir = tmp_path / "mock_dist"
    dist_dir.mkdir()
    large_asset = dist_dir / "large_asset.js"
    # Create > 2000 bytes file
    large_asset.write_text("console.log('test');\n" * 150, encoding="utf-8")

    custom_settings = Settings(
        SERVE_FRONTEND=True,
        FRONTEND_DIST_DIR=str(dist_dir),
    )
    test_app = create_app(settings_override=custom_settings)

    with TestClient(test_app) as custom_client:
        # Request with gzip support
        response = custom_client.get(
            "/large_asset.js",
            headers={"Accept-Encoding": "gzip"},
        )
        assert response.status_code == 200
        assert response.headers.get("content-encoding") == "gzip"
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("x-frame-options") == "DENY"
        assert (
            response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
        )
