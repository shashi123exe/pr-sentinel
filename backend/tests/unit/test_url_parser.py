"""Unit tests for GitHub Pull Request URL parser and validator."""

import pytest

from app.errors import AppError
from app.services.url_parser import parse_pr_url

VALID_TEST_CASES = [
    (
        "https://github.com/octocat/Hello-World/pull/42",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/octocat/Hello-World/pull/42/",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/octocat/Hello-World/pull/42/files",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/octocat/Hello-World/pull/42/files/",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/octocat/Hello-World/pull/42/commits",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/octocat/Hello-World/pull/42/commits/",
        "octocat",
        "Hello-World",
        42,
    ),
    (
        "https://github.com/my-org-123/my.project_repo-1/pull/9999999",
        "my-org-123",
        "my.project_repo-1",
        9999999,
    ),
    ("https://github.com/a/b/pull/1", "a", "b", 1),
    ("https://github.com/0/0/pull/1", "0", "0", 1),
]

INVALID_TEST_CASES = [
    # HTTP scheme
    ("http://github.com/octocat/Hello-World/pull/42", "HTTPS"),
    # www host
    ("https://www.github.com/octocat/Hello-World/pull/42", "github.com"),
    # Subdomain
    ("https://api.github.com/octocat/Hello-World/pull/42", "github.com"),
    # Lookalike domain
    ("https://github.com.evil.com/octocat/Hello-World/pull/42", "github.com"),
    ("https://evil-github.com/octocat/Hello-World/pull/42", "github.com"),
    # Userinfo
    ("https://user@github.com/octocat/Hello-World/pull/42", "user credentials"),
    (
        "https://user:pass@github.com/octocat/Hello-World/pull/42",
        "user credentials",
    ),
    # Explicit port
    ("https://github.com:443/octocat/Hello-World/pull/42", "explicit ports"),
    # IP address
    ("https://192.168.1.1/octocat/Hello-World/pull/42", "github.com"),
    # Unicode / punycode
    ("https://githüb.com/octocat/Hello-World/pull/42", "non-ASCII"),
    ("https://xn--githb-kva.com/octocat/Hello-World/pull/42", "github.com"),
    # Encoded slashes
    ("https://github.com/octocat%2FHello-World/pull/42", "encoded slashes"),
    ("https://github.com/octocat%2fHello-World/pull/42", "encoded slashes"),
    # Backslashes
    (r"https://github.com/octocat\Hello-World/pull/42", "backslashes"),
    # Whitespace
    ("https://github.com/octocat/ Hello-World/pull/42", "whitespace"),
    (" https://github.com/octocat/Hello-World/pull/42", "whitespace"),
    ("https://github.com/octocat/Hello-World/pull/42 ", "whitespace"),
    # Query string and fragment
    ("https://github.com/octocat/Hello-World/pull/42?tab=files", "query"),
    ("https://github.com/octocat/Hello-World/pull/42#section", "fragments"),
    # Missing / empty components
    ("https://github.com//Hello-World/pull/42", "path"),
    ("https://github.com/octocat//pull/42", "path"),
    # Invalid owner
    (
        "https://github.com/-octocat/Hello-World/pull/42",
        "Invalid repository owner",
    ),
    (
        "https://github.com/" + "a" * 40 + "/Hello-World/pull/42",
        "Invalid repository owner",
    ),
    # Invalid repo (. and ..)
    ("https://github.com/octocat/./pull/42", "Invalid repository name"),
    ("https://github.com/octocat/../pull/42", "Invalid repository name"),
    (
        "https://github.com/octocat/" + "a" * 101 + "/pull/42",
        "Invalid repository name",
    ),
    # Invalid PR number
    (
        "https://github.com/octocat/Hello-World/pull/0",
        "between 1 and 9,999,999",
    ),
    ("https://github.com/octocat/Hello-World/pull/-1", "path"),
    ("https://github.com/octocat/Hello-World/pull/abc", "path"),
    (
        "https://github.com/octocat/Hello-World/pull/10000000",
        "between 1 and 9,999,999",
    ),
    # Non-PR paths
    ("https://github.com/octocat/Hello-World/issues/42", "path"),
    ("https://github.com/octocat/Hello-World", "path"),
    # URL exceeding 300 characters
    ("https://github.com/octocat/" + "a" * 280 + "/pull/42", "300 characters"),
]


@pytest.mark.parametrize(
    "url,expected_owner,expected_repo,expected_number", VALID_TEST_CASES
)
def test_parse_pr_url_valid(url, expected_owner, expected_repo, expected_number):
    """Verify parser accepts valid GitHub PR URLs and parses components."""
    parsed = parse_pr_url(url)
    assert parsed.owner == expected_owner
    assert parsed.repo == expected_repo
    assert parsed.number == expected_number
    assert (
        parsed.canonical_url
        == f"https://github.com/{expected_owner}/{expected_repo}/pull/{expected_number}"
    )


@pytest.mark.parametrize("url,expected_err_substring", INVALID_TEST_CASES)
def test_parse_pr_url_invalid(url, expected_err_substring):
    """Verify parser rejects all malformed, insecure, and non-canonical URLs."""
    with pytest.raises(AppError) as exc_info:
        parse_pr_url(url)

    err = exc_info.value
    assert err.code == "invalid_pr_url"
    assert err.status_code == 422
    assert expected_err_substring.lower() in err.message.lower()
