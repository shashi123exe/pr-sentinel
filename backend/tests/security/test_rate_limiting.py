"""Security tests for rate limiting, throttling, and Retry-After headers."""

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.main import app
from app.rate_limit import limiter


class TestRateLimitingSecurity:
    """Security tests verifying rate limit enforcement on critical endpoints."""

    def test_post_reviews_rate_limit_exceeded(self, client: TestClient) -> None:
        """Verify POST /api/reviews throttles after 5 requests/minute with HTTP 429."""
        limiter.reset()

        # Send 5 requests (within rate limit)
        for i in range(5):
            resp = client.post(
                "/api/reviews",
                json={"pr_url": f"https://github.com/org/repo/pull/{i + 1}"},
            )
            # Either 202 accepted or downstream mock error, but NOT 429
            assert resp.status_code != 429, f"Premature rate limit at request {i + 1}"

        # 6th request within the same minute must be rate limited
        resp6 = client.post(
            "/api/reviews",
            json={"pr_url": "https://github.com/org/repo/pull/6"},
        )
        assert resp6.status_code == 429
        body = resp6.json()
        assert body["error"]["code"] == "rate_limit_exceeded"
        assert "Retry-After" in resp6.headers or "retry-after" in resp6.headers

        # Cleanup limiter state
        limiter.reset()

    def test_health_check_remains_available(self, client: TestClient) -> None:
        """Verify health check endpoint is resilient and not starved by low limit."""
        resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_proxy_headers_trusted_partitions_rate_limits_by_forwarded_ip(
        self, client: TestClient
    ) -> None:
        """Verify Uvicorn with --proxy-headers partitions rate limits by client IP.

        Security Tradeoff: On Render, the edge proxy terminates SSL and populates
        X-Forwarded-For with the verified client IP. Partitioning by client IP prevents
        one user's quota exhaustion from blocking other users.
        """
        limiter.reset()
        proxy_app = ProxyHeadersMiddleware(app, trusted_hosts="*")
        proxy_client = TestClient(proxy_app)
        try:
            # Client A (203.0.113.10) exhausts its 5 req/min quota
            for i in range(5):
                resp = proxy_client.post(
                    "/api/reviews",
                    json={"pr_url": f"https://github.com/org/repo/pull/{i + 10}"},
                    headers={"X-Forwarded-For": "203.0.113.10"},
                )
                assert resp.status_code != 429

            # 6th request from Client A is rejected
            resp_a6 = proxy_client.post(
                "/api/reviews",
                json={"pr_url": "https://github.com/org/repo/pull/16"},
                headers={"X-Forwarded-For": "203.0.113.10"},
            )
            assert resp_a6.status_code == 429
            assert resp_a6.json()["error"]["code"] == "rate_limit_exceeded"

            # Client B (198.51.100.50) has an independent quota and succeeds
            resp_b = proxy_client.post(
                "/api/reviews",
                json={"pr_url": "https://github.com/org/repo/pull/20"},
                headers={"X-Forwarded-For": "198.51.100.50"},
            )
            assert resp_b.status_code != 429
        finally:
            limiter.reset()

    def test_proxy_headers_untrusted_ignores_forwarded_for_and_prevents_spoofing(
        self, client: TestClient
    ) -> None:
        """Verify untrusted upstream clients cannot spoof X-Forwarded-For.

        This prevents an attacker from spoofing arbitrary X-Forwarded-For headers
        to bypass rate limits when connecting directly without a trusted proxy.
        """
        limiter.reset()
        # Trust only a specific private proxy IP (not the connecting peer 'testclient')
        proxy_app = ProxyHeadersMiddleware(app, trusted_hosts=["10.0.0.1"])
        proxy_client = TestClient(proxy_app)
        try:
            # Attacker sends requests attempting to spoof different X-Forwarded-For IPs
            for i in range(5):
                resp = proxy_client.post(
                    "/api/reviews",
                    json={"pr_url": f"https://github.com/org/repo/pull/{i + 30}"},
                    headers={"X-Forwarded-For": f"198.51.100.{i + 10}"},
                )
                assert resp.status_code != 429

            # 6th request with spoofed IP is STILL rate limited: the middleware
            # keyed on the actual client IP (testclient) because proxy was untrusted
            resp6 = proxy_client.post(
                "/api/reviews",
                json={"pr_url": "https://github.com/org/repo/pull/36"},
                headers={"X-Forwarded-For": "198.51.100.99"},
            )
            assert resp6.status_code == 429
            assert resp6.json()["error"]["code"] == "rate_limit_exceeded"
        finally:
            limiter.reset()

    def test_dockerfile_render_uvicorn_wildcard_proxy_argument(self) -> None:
        """Verify Dockerfile.render configures uvicorn forwarded-allow-ips='*' properly.

        Ensures shell quoting preserves the literal '*' and uvicorn.Config receives
        forwarded_allow_ips == '*' rather than escaped '\\*' or expanded paths.
        """
        import json
        import shlex
        from pathlib import Path

        import uvicorn

        dockerfile_path = Path(__file__).resolve().parents[3] / "Dockerfile.render"
        assert dockerfile_path.is_file(), (
            f"Dockerfile.render must exist at {dockerfile_path}"
        )

        content = dockerfile_path.read_text(encoding="utf-8")
        cmd_lines = [line for line in content.splitlines() if line.startswith("CMD ")]
        assert len(cmd_lines) == 1, "Expected exactly one CMD instruction in Dockerfile"

        cmd_raw = cmd_lines[0][4:].strip()
        cmd_tokens = json.loads(cmd_raw)
        assert cmd_tokens[0] == "sh"
        assert cmd_tokens[1] == "-c"
        shell_script = cmd_tokens[2]

        assert "--forwarded-allow-ips='*'" in shell_script
        assert "\\*" not in shell_script

        parsed_tokens = shlex.split(shell_script, posix=True)
        assert "--forwarded-allow-ips=*" in parsed_tokens

        # Verify uvicorn.Config parser parses this token to literal '*'
        arg_value = next(
            tok.split("=", 1)[1]
            for tok in parsed_tokens
            if tok.startswith("--forwarded-allow-ips=")
        )
        assert arg_value == "*"

        cfg = uvicorn.Config("app.main:app", forwarded_allow_ips=arg_value)
        assert cfg.forwarded_allow_ips == "*"
