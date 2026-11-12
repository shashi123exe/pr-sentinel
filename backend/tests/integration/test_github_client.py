"""Integration tests for GitHubClient with 100% mocked HTTP via respx."""

import base64
import logging

import httpx
import pytest
import respx

from app.db.models import AppSettings
from app.errors import AppError
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot
from app.services.github_client import GITHUB_API_BASE_URL, GitHubClient
from app.services.url_parser import ParsedPR

SAMPLE_PR_PAYLOAD = {
    "title": "Add feature X",
    "user": {"login": "octocat"},
    "base": {"sha": "basesha123", "ref": "main"},
    "head": {"sha": "headsha456", "ref": "feature-x"},
    "state": "open",
    "merged": False,
    "additions": 42,
    "deletions": 10,
    "changed_files": 2,
    "html_url": "https://github.com/octocat/Hello-World/pull/1",
}


@pytest.mark.asyncio
@respx.mock
async def test_get_pull_metadata_success():
    """Scenario 1: Successful PR metadata retrieval (GET /pulls/{number})."""
    url = "https://api.github.com/repos/octocat/Hello-World/pulls/1"
    route = respx.get(url).respond(
        status_code=200,
        json=SAMPLE_PR_PAYLOAD,
    )

    async with GitHubClient(token="ghp_test_token") as client:
        pr = await client.get_pull("octocat", "Hello-World", 1)

    assert route.called
    assert pr.title == "Add feature X"
    assert pr.author == "octocat"
    assert pr.base_sha == "basesha123"
    assert pr.head_sha == "headsha456"
    assert pr.base_ref == "main"
    assert pr.head_ref == "feature-x"
    assert pr.state == "open"
    assert pr.merged is False
    assert pr.additions == 42
    assert pr.deletions == 10
    assert pr.changed_files == 2
    assert pr.html_url == "https://github.com/octocat/Hello-World/pull/1"


@pytest.mark.asyncio
@respx.mock
async def test_list_pull_files_pagination():
    """Scenario 2: Successful file listing across two pages."""
    page_1_data = [
        {
            "filename": f"file_{i}.py",
            "status": "added",
            "additions": 1,
            "deletions": 0,
        }
        for i in range(100)
    ]
    page_2_data = [
        {
            "filename": f"file_10{i}.py",
            "status": "modified",
            "additions": 2,
            "deletions": 1,
        }
        for i in range(5)
    ]

    r1 = respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "1"},
    ).respond(status_code=200, json=page_1_data)

    r2 = respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "2"},
    ).respond(status_code=200, json=page_2_data)

    async with GitHubClient(token="ghp_test") as client:
        files, is_truncated = await client.list_pull_files(
            "octocat", "Hello-World", 1, max_files=200
        )

    assert r1.called
    assert r2.called
    assert len(files) == 105
    assert is_truncated is False
    assert files[0].path == "file_0.py"
    assert files[104].path == "file_104.py"


@pytest.mark.asyncio
@respx.mock
async def test_get_pull_not_found_404():
    """Scenario 3: 404 handling maps to AppError(code='pr_not_found', status=404)."""
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/999").respond(
        status_code=404,
        json={
            "message": "Not Found",
            "documentation_url": "https://docs.github.com/rest",
        },
    )

    async with GitHubClient(token="ghp_test") as client:
        with pytest.raises(AppError) as exc_info:
            await client.get_pull("octocat", "Hello-World", 999)

    assert exc_info.value.code == "pr_not_found"
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_handling_403():
    """Scenario 4: 403 rate-limit maps to code='github_rate_limited', status=429."""
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1730680000"},
        json={"message": "API rate limit exceeded for user"},
    )

    async with GitHubClient(token="ghp_test") as client:
        with pytest.raises(AppError) as exc_info:
            await client.get_pull("octocat", "Hello-World", 1)

    assert exc_info.value.code == "github_rate_limited"
    assert exc_info.value.status_code == 429
    assert "rate limit exceeded" in exc_info.value.message.lower()
    assert "resets at" in exc_info.value.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_401():
    """Scenario 5: 401 auth failure maps to code='github_auth_failed', status=401."""
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=401,
        json={"message": "Bad credentials"},
    )

    async with GitHubClient(token="ghp_invalid_token") as client:
        with pytest.raises(AppError) as exc_info:
            await client.get_pull("octocat", "Hello-World", 1)

    assert exc_info.value.code == "github_auth_failed"
    assert exc_info.value.status_code == 401
    assert "authentication failed" in exc_info.value.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_5xx_success():
    """Scenario 6: 5xx retry: 502/503 on first attempt, 200 on retry; succeeds."""
    route = respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").mock(
        side_effect=[
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(200, json=SAMPLE_PR_PAYLOAD),
        ]
    )

    async with GitHubClient(
        token="ghp_test", retry_attempts=2, backoff_factor=0.001
    ) as client:
        pr = await client.get_pull("octocat", "Hello-World", 1)

    assert route.call_count == 2
    assert pr.title == "Add feature X"


@pytest.mark.asyncio
@respx.mock
async def test_truncation_at_max_files():
    """Scenario 7: Truncation at max_files: assert limited files and truncated=True."""
    files_data = [
        {
            "filename": f"file_{i}.py",
            "status": "modified",
            "additions": 1,
            "deletions": 0,
        }
        for i in range(5)
    ]

    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "1"},
    ).respond(status_code=200, json=files_data)

    async with GitHubClient(token="ghp_test") as client:
        files, is_truncated = await client.list_pull_files(
            "octocat", "Hello-World", 1, max_files=2
        )

    assert len(files) == 2
    assert is_truncated is True
    assert files[0].path == "file_0.py"
    assert files[1].path == "file_1.py"


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_base64_success():
    """Scenario 8: Base64 file content retrieval and decoding."""
    raw_text = "def hello():\n    return 'world'\n"
    encoded_b64 = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")

    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/contents/app/main.py",
        params={"ref": "headsha456"},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": len(raw_text),
            "name": "main.py",
            "path": "app/main.py",
            "encoding": "base64",
            "content": encoded_b64,
        },
    )

    async with GitHubClient(token="ghp_test") as client:
        content = await client.get_file_content(
            "octocat", "Hello-World", "app/main.py", "headsha456"
        )

    assert content == raw_text


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_oversized_returns_none():
    """Scenario 9: Oversized file handling (size > max_bytes returns None)."""
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/contents/large_data.json",
        params={"ref": "headsha456"},
    ).respond(
        status_code=200,
        json={
            "type": "file",
            "size": 600000,
            "name": "large_data.json",
            "path": "large_data.json",
            "encoding": "base64",
            "content": "abc",
        },
    )

    async with GitHubClient(token="ghp_test") as client:
        content = await client.get_file_content(
            "octocat",
            "Hello-World",
            "large_data.json",
            "headsha456",
            max_bytes=500000,
        )

    assert content is None


@pytest.mark.asyncio
@respx.mock
async def test_path_traversal_rejection_no_network_call():
    """Scenario 10: Path traversal rejection returns None without network call."""
    route = respx.get(url__regex=r"^https://api\.github\.com/repos/.*").respond(200)

    async with GitHubClient(token="ghp_test") as client:
        r1 = await client.get_file_content(
            "octocat", "Hello-World", "../secret.env", "main"
        )
        r2 = await client.get_file_content(
            "octocat", "Hello-World", "/etc/passwd", "main"
        )
        r3 = await client.get_file_content(
            "octocat", "Hello-World", "src/../../passwords.txt", "main"
        )
        r4 = await client.get_file_content("octocat", "Hello-World", "", "main")

    assert r1 is None
    assert r2 is None
    assert r3 is None
    assert r4 is None
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_timeout_retry_success():
    """Scenario 11: Timeout retry: Timeout on attempt 1, success on attempt 2."""
    route = respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").mock(
        side_effect=[
            httpx.ReadTimeout("Read timed out"),
            httpx.Response(200, json=SAMPLE_PR_PAYLOAD),
        ]
    )

    async with GitHubClient(
        token="ghp_test", retry_attempts=2, backoff_factor=0.001
    ) as client:
        pr = await client.get_pull("octocat", "Hello-World", 1)

    assert route.call_count == 2
    assert pr.title == "Add feature X"


@pytest.mark.asyncio
@respx.mock
async def test_timeout_retry_exhaustion_raises_app_error():
    """Scenario 11 (b): Timeout retry exhaustion raises code='github_unavailable'."""
    route = respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").mock(
        side_effect=[
            httpx.ReadTimeout("Timeout 1"),
            httpx.ReadTimeout("Timeout 2"),
        ]
    )

    async with GitHubClient(
        token="ghp_test", retry_attempts=1, backoff_factor=0.001
    ) as client:
        with pytest.raises(AppError) as exc_info:
            await client.get_pull("octocat", "Hello-World", 1)

    assert route.call_count == 2
    assert exc_info.value.code == "github_unavailable"
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_metadata_and_changed_file_parsing():
    """Scenario 12: Correct metadata and changed-file parsing."""
    raw_pr = {
        "title": "Fix security vulnerability",
        "user": {"login": "mona"},
        "base": {"sha": "1" * 40, "ref": "develop"},
        "head": {"sha": "2" * 40, "ref": "fix/vuln"},
        "state": "closed",
        "merged": True,
        "additions": 150,
        "deletions": 40,
        "changed_files": 4,
        "html_url": "https://github.com/org/repo/pull/99",
    }
    meta = PRMetadata.from_github_api(raw_pr)
    assert meta.title == "Fix security vulnerability"
    assert meta.author == "mona"
    assert meta.base_sha == "1" * 40
    assert meta.head_sha == "2" * 40
    assert meta.base_ref == "develop"
    assert meta.head_ref == "fix/vuln"
    assert meta.state == "merged"
    assert meta.merged is True
    assert meta.additions == 150
    assert meta.deletions == 40
    assert meta.changed_files == 4
    assert meta.html_url == "https://github.com/org/repo/pull/99"

    raw_file = {
        "filename": "new_name.py",
        "status": "renamed",
        "previous_filename": "old_name.py",
        "additions": 10,
        "deletions": 5,
        "patch": "@@ -1,5 +1,10 @@",
        "sha": "abcdef123456",
        "raw_size": 2048,
    }
    changed_file = ChangedFile.from_github_api(raw_file)
    assert changed_file.path == "new_name.py"
    assert changed_file.status == "renamed"
    assert changed_file.previous_path == "old_name.py"
    assert changed_file.additions == 10
    assert changed_file.deletions == 5
    assert changed_file.patch == "@@ -1,5 +1,10 @@"
    assert changed_file.sha == "abcdef123456"
    assert changed_file.raw_size == 2048


@pytest.mark.asyncio
async def test_fixed_origin_enforcement():
    """Scenario 13: Verify fixed origin: assert only api.github.com is permitted."""
    async with GitHubClient(token="ghp_test") as client:
        assert str(client._client.base_url).rstrip("/") == GITHUB_API_BASE_URL

        # Calling arbitrary hosts must raise github_security_violation
        with pytest.raises(AppError) as exc_info:
            await client._request("GET", "https://evil.com/malicious/endpoint")

        assert exc_info.value.code == "github_security_violation"
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
@respx.mock
async def test_authorization_header_behavior():
    """Scenario 14: Verify Authorization header presence based on token."""
    route = respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1"
    ).respond(status_code=200, json=SAMPLE_PR_PAYLOAD)

    # 1. Without token
    async with GitHubClient(token="") as client_no_token:
        await client_no_token.get_pull("octocat", "Hello-World", 1)

    req_no_token = route.calls[0].request
    assert "Authorization" not in req_no_token.headers

    # 2. With token
    async with GitHubClient(token="ghp_my_custom_pat_token") as client_with_token:
        await client_with_token.get_pull("octocat", "Hello-World", 1)

    req_with_token = route.calls[1].request
    assert req_with_token.headers["Authorization"] == "Bearer ghp_my_custom_pat_token"


@pytest.mark.asyncio
@respx.mock
async def test_sensitive_token_never_logged(caplog: pytest.LogCaptureFixture):
    """Scenario 15: Verify Authorization token values never appear in log output."""
    secret_pat = "ghp_sensitive_secret_token_1234567890abcdef"

    # Trigger a 502 then 200 to generate retry warning logs
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").mock(
        side_effect=[
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(200, json=SAMPLE_PR_PAYLOAD),
        ]
    )

    with caplog.at_level(logging.DEBUG):
        async with GitHubClient(
            token=secret_pat, retry_attempts=2, backoff_factor=0.001
        ) as client:
            await client.get_pull("octocat", "Hello-World", 1)

    # Ensure token does NOT appear in captured log output
    assert secret_pat not in caplog.text


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pr_snapshot_orchestration():
    """Verify fetch_pr_snapshot combines metadata, files, and handles truncation."""
    parsed = ParsedPR(owner="octocat", repo="Hello-World", number=1)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 10},
    )
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "1"},
    ).respond(
        status_code=200,
        json=[
            {"filename": "a.py", "status": "added"},
            {"filename": "b.py", "status": "modified"},
        ],
    )

    settings = AppSettings(max_files=50)

    async with GitHubClient(token="ghp_test") as client:
        snapshot = await client.fetch_pr_snapshot(parsed, settings)

    assert isinstance(snapshot, PRSnapshot)
    assert snapshot.metadata.title == "Add feature X"
    assert len(snapshot.files) == 2
    assert snapshot.truncated is True


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_directory_or_non_base64():
    """Verify get_file_content returns None when target is directory or non-base64."""
    # 1. Directory response
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/contents/docs",
        params={"ref": "main"},
    ).respond(
        status_code=200,
        json=[{"name": "index.md", "type": "file"}],
    )

    # 2. Non-base64 encoding
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/contents/unknown.dat",
        params={"ref": "main"},
    ).respond(
        status_code=200,
        json={"type": "file", "size": 10, "encoding": "none", "content": "raw"},
    )

    async with GitHubClient(token="ghp_test") as client:
        r_dir = await client.get_file_content("octocat", "Hello-World", "docs", "main")
        r_enc = await client.get_file_content(
            "octocat", "Hello-World", "unknown.dat", "main"
        )

    assert r_dir is None
    assert r_enc is None


@pytest.mark.asyncio
@respx.mock
async def test_truncation_false_when_exact_max_files_retrieved():
    """Regression: snapshot.truncated must be False when exactly max_files exist."""
    # Subtest 1: 2 files total, max_files=2 (batch < per_page)
    parsed = ParsedPR(owner="octocat", repo="Hello-World", number=1)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 2},
    )
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "1"},
    ).respond(
        status_code=200,
        json=[
            {"filename": "file_1.py", "status": "added"},
            {"filename": "file_2.py", "status": "modified"},
        ],
    )

    settings = AppSettings(max_files=2)
    async with GitHubClient(token="ghp_test") as client:
        snapshot = await client.fetch_pr_snapshot(parsed, settings)

    assert len(snapshot.files) == 2
    assert snapshot.truncated is False

    # Subtest 2: exactly 100 files total, max_files=100 (batch == per_page)
    parsed_100 = ParsedPR(owner="octocat", repo="Hello-World", number=2)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/2").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 100},
    )
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/2/files",
        params={"per_page": "100", "page": "1"},
    ).respond(
        status_code=200,
        json=[{"filename": f"f_{i}.py", "status": "modified"} for i in range(100)],
    )

    settings_100 = AppSettings(max_files=100)
    async with GitHubClient(token="ghp_test") as client:
        snapshot_100 = await client.fetch_pr_snapshot(parsed_100, settings_100)

    assert len(snapshot_100.files) == 100
    assert snapshot_100.truncated is False


@pytest.mark.asyncio
@respx.mock
async def test_truncation_true_when_more_than_max_files_exist():
    """Regression: snapshot.truncated must be True when more than max_files exist."""
    # Subtest 1: 5 files on page 1, but max_files=2
    parsed = ParsedPR(owner="octocat", repo="Hello-World", number=1)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 5},
    )
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/1/files",
        params={"per_page": "100", "page": "1"},
    ).respond(
        status_code=200,
        json=[{"filename": f"file_{i}.py", "status": "modified"} for i in range(5)],
    )

    settings = AppSettings(max_files=2)
    async with GitHubClient(token="ghp_test") as client:
        snapshot = await client.fetch_pr_snapshot(parsed, settings)

    assert len(snapshot.files) == 2
    assert snapshot.truncated is True

    # Subtest 2: 105 files total across pages, max_files=100
    parsed_100 = ParsedPR(owner="octocat", repo="Hello-World", number=2)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/2").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 105},
    )
    respx.get(
        "https://api.github.com/repos/octocat/Hello-World/pulls/2/files",
        params={"per_page": "100", "page": "1"},
    ).respond(
        status_code=200,
        json=[{"filename": f"f_{i}.py", "status": "modified"} for i in range(100)],
    )

    settings_100 = AppSettings(max_files=100)
    async with GitHubClient(token="ghp_test") as client:
        snapshot_100 = await client.fetch_pr_snapshot(parsed_100, settings_100)

    assert len(snapshot_100.files) == 100
    assert snapshot_100.truncated is True


@pytest.mark.asyncio
@respx.mock
async def test_truncation_true_when_github_api_cap_exceeded():
    """Regression: snapshot.truncated must be True when exceeding GitHub 3,000 cap."""
    parsed = ParsedPR(owner="octocat", repo="Hello-World", number=1)
    respx.get("https://api.github.com/repos/octocat/Hello-World/pulls/1").respond(
        status_code=200,
        json={**SAMPLE_PR_PAYLOAD, "changed_files": 3500},
    )

    # Route pattern returning 100 files for each of the 30 pages
    respx.get(
        url__regex=r"^https://api\.github\.com/repos/octocat/Hello-World/pulls/1/files\?.*"
    ).respond(
        status_code=200,
        json=[{"filename": f"f_{i}.py", "status": "modified"} for i in range(100)],
    )

    settings = AppSettings(max_files=5000)
    async with GitHubClient(token="ghp_test") as client:
        snapshot = await client.fetch_pr_snapshot(parsed, settings)

    assert len(snapshot.files) == 3000
    assert snapshot.truncated is True
