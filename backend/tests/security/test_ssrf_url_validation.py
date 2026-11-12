"""Security tests for SSRF prevention, URL validation, and destination pinning."""

import pytest
import respx
from pydantic import SecretStr

from app.errors import AppError
from app.services.github_client import GitHubClient
from app.services.url_parser import ParsedPR, parse_pr_url

MALICIOUS_PR_URLS = [
    # 1. Lookalike domains
    ("https://github.com.evil.com/owner/repo/pull/1", "subdomain lookalike"),
    ("https://github.com.attacker.net/owner/repo/pull/1", "suffix lookalike"),
    ("https://evilgithub.com/owner/repo/pull/1", "prefixed domain lookalike"),
    ("https://github.co/owner/repo/pull/1", "tld lookalike .co"),
    ("https://github.net/owner/repo/pull/1", "tld lookalike .net"),
    ("https://github.org/owner/repo/pull/1", "tld lookalike .org"),
    ("https://fake-github.com/owner/repo/pull/1", "hyphenated lookalike"),
    ("https://github.com.br/owner/repo/pull/1", "country code suffix"),
    ("https://notgithub.com/owner/repo/pull/1", "notgithub domain"),
    # 2. Incorrect protocols
    ("http://github.com/owner/repo/pull/1", "plain http scheme"),
    ("ftp://github.com/owner/repo/pull/1", "ftp protocol"),
    ("file:///etc/passwd", "file protocol local path"),
    ("gopher://github.com/owner/repo/pull/1", "gopher protocol"),
    ("ws://github.com/owner/repo/pull/1", "websocket scheme"),
    ("ssh://git@github.com/owner/repo/pull/1", "ssh protocol"),
    # 3. User information (credential embedding)
    ("https://admin:password@github.com/owner/repo/pull/1", "embedded user creds"),
    ("https://user@github.com/owner/repo/pull/1", "embedded username"),
    ("https://git@github.com/owner/repo/pull/1", "git username prefix"),
    # 4. Explicit ports
    ("https://github.com:443/owner/repo/pull/1", "explicit port 443"),
    ("https://github.com:8080/owner/repo/pull/1", "explicit port 8080"),
    ("https://github.com:80/owner/repo/pull/1", "explicit port 80"),
    ("https://github.com:22/owner/repo/pull/1", "explicit port 22"),
    # 5. IP addresses
    ("https://192.168.1.1/owner/repo/pull/1", "private IPv4"),
    ("https://8.8.8.8/owner/repo/pull/1", "public IPv4"),
    ("https://[::1]/owner/repo/pull/1", "IPv6 loopback"),
    ("https://[2001:db8::1]/owner/repo/pull/1", "IPv6 address"),
    ("https://2130706433/owner/repo/pull/1", "decimal IP for 127.0.0.1"),
    ("https://0x7f000001/owner/repo/pull/1", "hexadecimal IP for 127.0.0.1"),
    # 6. Internal destinations
    ("https://localhost/owner/repo/pull/1", "localhost host"),
    ("https://127.0.0.1/owner/repo/pull/1", "IPv4 loopback"),
    ("https://10.0.0.1/owner/repo/pull/1", "10.x private range"),
    ("https://172.16.0.1/owner/repo/pull/1", "172.16.x private range"),
    ("https://169.254.169.254/owner/repo/pull/1", "cloud metadata endpoint"),
    # 7. Encoding attacks
    ("https://github.com/owner%2frepo/pull/1", "percent-encoded slash in path"),
    ("https://github.com/owner/repo/pull%2f1", "percent-encoded slash before num"),
    ("https://github.com/owner/repo/pull/%252f1", "double percent-encoded slash"),
    ("https://github.com\\owner\\repo\\pull\\1", "backslash path separators"),
    ("https://github.com/owner/repo\\pull/1", "mixed backslash in path"),
    ("https://\u0261ithub.com/owner/repo/pull/1", "unicode lookalike small latin g"),
    ("https://xn--gthub-1qa.com/owner/repo/pull/1", "punycode lookalike domain"),
    # 8. Header injection / CRLF / embedded whitespace
    ("https://github.com/owner/repo/pull/1\r\nX-Injected: evil", "CRLF injection"),
    ("https://github.com/owner/repo/pull/1\nInjected-Header: 1", "newline injection"),
    ("https://github.com/owner/repo/pull/1\rInjected-Header: 1", "CR injection"),
    ("https://github.com/owner/repo/pull/1\t", "embedded tab separator"),
    ("https://github.com/owner/repo/pull/ 1", "embedded whitespace in path"),
    (" https://github.com/owner/repo/pull/1", "leading whitespace"),
    ("https://github.com/owner/repo/pull/1 ", "trailing whitespace"),
    # 9. URL structure attacks
    ("https://github.com/owner/repo/pull/1?token=leak", "query string token leak"),
    ("https://github.com/owner/repo/pull/1#section", "URL fragment"),
    ("https://github.com/owner/repo/pull/1/extra/segment", "extra path segment"),
    ("https://github.com/owner/repo", "missing PR number"),
    ("https://github.com/owner/repo/issues/1", "issue URL instead of PR"),
    ("https://github.com/owner/repo/pull/0", "zero PR number"),
    ("https://github.com/owner/repo/pull/-1", "negative PR number"),
    ("https://github.com/owner/repo/pull/10000000", "PR number exceeding 7 digits"),
    ("https://github.com/owner/repo/pull/abc", "non-numeric PR number"),
    ("https://github.com/" + "a" * 310 + "/repo/pull/1", "URL over 300 chars"),
    ("https://github.com//repo/pull/1", "empty owner in path"),
    ("https://github.com/owner//pull/1", "empty repo in path"),
    ("https://github.com/owner/../repo/pull/1", "directory traversal in path"),
    ("https://github.com/owner/./repo/pull/1", "dot traversal in path"),
]


class TestSSRFAndURLValidation:
    """Rigorous security testing verifying rejection of malicious PR URLs."""

    @pytest.mark.parametrize("url,description", MALICIOUS_PR_URLS)
    def test_malicious_urls_rejected(self, url: str, description: str):
        """Verify that every malicious, lookalike, or malformed URL is rejected."""
        with pytest.raises(AppError) as exc_info:
            parse_pr_url(url)
        assert exc_info.value.code == "invalid_pr_url", f"Failed: {description}: {url}"
        assert exc_info.value.status_code == 422

    def test_non_string_inputs_rejected(self):
        """Verify non-string inputs are rejected safely with HTTP 422."""
        for invalid_val in [None, 12345, [], {}, True]:
            with pytest.raises(AppError) as exc_info:
                parse_pr_url(invalid_val)  # type: ignore[arg-type]
            assert exc_info.value.code == "invalid_pr_url"
            assert exc_info.value.status_code == 422

    def test_canonical_valid_urls_accepted(self):
        """Verify strictly compliant canonical GitHub PR URLs succeed."""
        valid_urls = [
            "https://github.com/pallets/flask/pull/6176",
            "https://github.com/fastapi/fastapi/pull/10000",
            "https://github.com/facebook/react/pull/1",
            "https://github.com/a-b-c/my.repo_name/pull/9999999",
            "https://github.com/owner/repo/pull/42/files",
            "https://github.com/owner/repo/pull/42/commits",
        ]
        for vurl in valid_urls:
            parsed = parse_pr_url(vurl)
            assert isinstance(parsed, ParsedPR)
            assert parsed.owner
            assert parsed.repo
            assert parsed.number >= 1
            assert parsed.canonical_url.startswith("https://github.com/")


class TestGitHubClientSSRFProtections:
    """respx-based security tests for destination pinning and redirect blocking."""

    @pytest.mark.asyncio
    @respx.mock
    async def test_outbound_requests_pinned_to_api_github_com(self):
        """Verify outbound HTTP requests strictly target https://api.github.com."""
        route = respx.get(
            "https://api.github.com/repos/test-org/test-repo/pulls/10"
        ).respond(
            200,
            json={
                "id": 1,
                "number": 10,
                "title": "Test PR",
                "user": {"login": "dev"},
                "base": {"sha": "a" * 40},
                "head": {"sha": "b" * 40},
                "state": "open",
                "html_url": "https://github.com/test-org/test-repo/pull/10",
                "diff_url": "https://github.com/test-org/test-repo/pull/10.diff",
                "created_at": "2026-11-04T00:00:00Z",
                "changed_files": 1,
                "additions": 5,
                "deletions": 2,
            },
        )

        async with GitHubClient(token=SecretStr("fake_token")) as client:
            parsed = ParsedPR(owner="test-org", repo="test-repo", number=10)
            meta = await client.get_pull(parsed.owner, parsed.repo, parsed.number)

        assert meta.title == "Test PR"
        assert meta.author == "dev"
        assert route.called
        expected_url = "https://api.github.com/repos/test-org/test-repo/pulls/10"
        assert str(route.calls[0].request.url) == expected_url

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirects_are_not_followed(self):
        """Verify HTTP redirects (301/302) to external hosts are never followed."""
        respx.get("https://api.github.com/repos/test-org/test-repo/pulls/10").respond(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
        )
        attacker_route = respx.get("http://169.254.169.254/latest/meta-data/").respond(
            200, text="SECRET_METADATA"
        )

        async with GitHubClient(token=SecretStr("fake_token")) as client:
            parsed = ParsedPR(owner="test-org", repo="test-repo", number=10)
            with pytest.raises(AppError) as exc_info:
                await client.get_pull(parsed.owner, parsed.repo, parsed.number)
            expected_codes = {
                "github_upstream_error",
                "github_error",
                "github_client_error",
            }
            assert exc_info.value.code in expected_codes

        assert not attacker_route.called

    @pytest.mark.asyncio
    @respx.mock
    async def test_mocked_redirect_to_evil_host_blocked(self):
        """Verify redirect to evil.com is blocked and token is not leaked."""
        respx.get("https://api.github.com/repos/test-org/test-repo/pulls/10").respond(
            301,
            headers={"Location": "https://evil.attacker.com/steal-token"},
        )
        evil_route = respx.get("https://evil.attacker.com/steal-token").respond(200)

        async with GitHubClient(token=SecretStr("secret_token_123")) as client:
            parsed = ParsedPR(owner="test-org", repo="test-repo", number=10)
            with pytest.raises(AppError):
                await client.get_pull(parsed.owner, parsed.repo, parsed.number)

        assert not evil_route.called

    @pytest.mark.asyncio
    async def test_arbitrary_urls_rejected_by_internal_request(self):
        """Verify _request rejects URLs not starting with api.github.com."""
        async with GitHubClient(token=SecretStr("token")) as client:
            for evil_url in [
                "http://localhost:8000/internal",
                "https://evil.com/api",
                "ftp://api.github.com/repos",
                "https://api.github.com.evil.com/repos",
            ]:
                with pytest.raises(AppError) as exc_info:
                    await client._request("GET", evil_url)
                assert exc_info.value.code == "github_security_violation"
