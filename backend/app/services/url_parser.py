"""GitHub Pull Request URL parser and strict validator."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import status

from app.errors import AppError

# Regex specifications
OWNER_REGEX = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_REGEX = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
PATH_REGEX = re.compile(
    r"^/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)/pull/(\d+)(?:/(?:files|commits))?/?$"
)


@dataclass(frozen=True)
class ParsedPR:
    """Validated components of a GitHub pull request URL."""

    owner: str
    repo: str
    number: int

    @property
    def canonical_url(self) -> str:
        """Normalized canonical GitHub pull request URL."""
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


def parse_pr_url(url: str) -> ParsedPR:
    """Strictly validate and parse a GitHub Pull Request URL.

    Security & Validation Constraints:
    - Must be HTTPS scheme on host exactly 'github.com'.
    - Rejects subdomains, lookalike domains, userinfo, explicit ports, IP addresses.
    - Rejects unicode/punycode, backslashes, encoded slashes, whitespace.
    - Rejects query parameters and fragments.
    - Maximum length of 300 characters.
    - Owner must match GitHub username rules (1-39 chars, alphanumeric/hyphen).
    - Repo must match GitHub repo rules (1-100 chars), excluding '.' and '..'.
    - PR number must be an integer between 1 and 9,999,999.
    """
    if not isinstance(url, str):
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must be a string.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 1. Length constraint
    if len(url) > 300:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL exceeds maximum length of 300 characters.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 2. Reject whitespace
    if re.search(r"\s", url):
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not contain whitespace.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 3. Reject backslashes
    if "\\" in url:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not contain backslashes.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 4. Reject encoded slashes (%2f, %2F)
    if "%2f" in url.lower():
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not contain encoded slashes.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 5. Reject non-ASCII / Unicode / punycode characters
    try:
        url.encode("ascii")
    except UnicodeEncodeError:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL contains invalid non-ASCII characters.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from None

    # 6. Parse URL structure
    try:
        split_url = urlsplit(url)
    except Exception:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL could not be parsed.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from None

    # 7. Strict HTTPS scheme
    if split_url.scheme != "https":
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must use the HTTPS protocol.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 8. Exact host check: github.com only
    # urlsplit('https://user@github.com') sets netloc='user@github.com'
    netloc = split_url.netloc
    if "@" in netloc or ":" in netloc:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not include user credentials or explicit ports.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if netloc != "github.com":
        raise AppError(
            code="invalid_pr_url",
            message="PR URL host must be exactly 'github.com'.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 9. No query string or fragment allowed
    if split_url.query:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not contain query parameters.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    if split_url.fragment:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL must not contain URL fragments.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # 10. Path validation
    match = PATH_REGEX.match(split_url.path)
    if not match:
        raise AppError(
            code="invalid_pr_url",
            message="PR URL path must match '/{owner}/{repo}/pull/{number}'.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    raw_owner, raw_repo, raw_number = match.groups()

    # Validate Owner
    if not OWNER_REGEX.match(raw_owner):
        raise AppError(
            code="invalid_pr_url",
            message=f"Invalid repository owner '{raw_owner}'.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # Validate Repo
    if not REPO_REGEX.match(raw_repo) or raw_repo in {".", ".."}:
        raise AppError(
            code="invalid_pr_url",
            message=f"Invalid repository name '{raw_repo}'.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    # Validate Pull Request Number
    try:
        pr_number = int(raw_number)
    except ValueError:
        raise AppError(
            code="invalid_pr_url",
            message="PR number must be a valid integer.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from None

    if pr_number < 1 or pr_number >= 10_000_000:
        raise AppError(
            code="invalid_pr_url",
            message="PR number must be between 1 and 9,999,999.",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    return ParsedPR(owner=raw_owner, repo=raw_repo, number=pr_number)
