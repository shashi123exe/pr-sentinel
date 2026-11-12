"""Integration tests for SlowAPI rate limiting."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.rate_limit import limiter


def test_rate_limiting_post_reviews(client: TestClient):
    """Verify that the 6th POST request within one minute returns HTTP 429."""
    limiter.reset()
    try:
        payload = {"pr_url": "https://github.com/org/repo/pull/1"}
        with patch("app.api.reviews.run_review", new_callable=AsyncMock):
            # First 5 requests should succeed (202)
            for i in range(5):
                res = client.post("/api/reviews", json=payload)
                assert res.status_code == 202, (
                    f"Request {i + 1} failed with status {res.status_code}"
                )

            # 6th request must trigger rate limit (429)
            sixth_res = client.post("/api/reviews", json=payload)
            assert sixth_res.status_code == 429

            data = sixth_res.json()
            assert "error" in data
            assert data["error"]["code"] == "rate_limit_exceeded"
            assert "Rate limit exceeded" in data["error"]["message"]
            assert "request_id" in data["error"]
            assert data["error"]["request_id"] != ""

            # Check Retry-After response header
            assert "Retry-After" in sixth_res.headers
    finally:
        limiter.reset()
