"""GitHub REST API client for pull request and repository retrieval."""

import asyncio
import base64
import datetime
import logging
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from app.config import get_settings
from app.db.models import AppSettings
from app.errors import AppError
from app.schemas.github import ChangedFile, PRMetadata, PRSnapshot
from app.services.url_parser import ParsedPR

logger = logging.getLogger(__name__)

# Fixed GitHub API origin - never deviated from
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
USER_AGENT = "pr-sentinel"
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 20.0
GITHUB_MAX_FILE_CAP = 3000


class GitHubClient:
    """Asynchronous client for interacting with the GitHub REST API.

    Security & Resilience Guarantees:
    - Fixed api.github.com origin (no redirects followed, no arbitrary hosts).
    - Connect timeout: 5s, Read timeout: 20s.
    - Idempotent request retry with exponential backoff on 5xx and timeouts.
    - Token values are never logged or exposed.
    """

    def __init__(
        self,
        token: SecretStr | str | None = None,
        client: httpx.AsyncClient | None = None,
        retry_attempts: int = 2,
        backoff_factor: float = 0.5,
    ) -> None:
        self._retry_attempts = retry_attempts
        self._backoff_factor = backoff_factor

        # Resolve GitHub token from parameter or app settings
        if token is not None:
            if isinstance(token, str):
                self._token: SecretStr | None = SecretStr(token) if token else None
            else:
                self._token = token
        else:
            settings = get_settings()
            self._token = settings.GITHUB_TOKEN

        # Configure headers
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token is not None:
            raw_token = self._token.get_secret_value().strip()
            if raw_token:
                headers["Authorization"] = f"Bearer {raw_token}"

        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)

        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(
                base_url=GITHUB_API_BASE_URL,
                headers=headers,
                timeout=timeout,
                follow_redirects=False,
            )
            self._owns_client = True

    async def aclose(self) -> None:
        """Close underlying HTTP client if owned."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform request against GitHub API with retry logic and error mapping."""
        if "://" in path:
            url = path
        elif path.startswith("/"):
            url = f"{GITHUB_API_BASE_URL}{path}"
        else:
            url = f"{GITHUB_API_BASE_URL}/{path}"

        if not (
            url == GITHUB_API_BASE_URL
            or url.startswith(f"{GITHUB_API_BASE_URL}/")
            or url.startswith(f"{GITHUB_API_BASE_URL}?")
        ):
            raise AppError(
                code="github_security_violation",
                message="Disallowed request host. Only api.github.com is permitted.",
                status_code=500,
            )

        attempts = 1 + self._retry_attempts
        last_exc: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    params=params,
                )

                # Retry on 502, 503, 504 server errors
                if response.status_code in {502, 503, 504} and attempt < attempts:
                    delay = self._backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "GitHub API returned %d. Retrying (%d/%d) in %.2fs...",
                        response.status_code,
                        attempt,
                        self._retry_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                if response.is_success:
                    return response

                # Handle client and server error responses
                self._handle_error_response(response)

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
                if attempt < attempts:
                    delay = self._backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        "GitHub request error (%s). Retrying (%d/%d) in %.2fs...",
                        exc.__class__.__name__,
                        attempt,
                        self._retry_attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("GitHub API request failed after retries: %s", exc)
                raise AppError(
                    code="github_unavailable",
                    message="GitHub API is currently unavailable or timed out.",
                    status_code=503,
                ) from exc

        # Fallback if loop exited without response
        if last_exc:
            raise AppError(
                code="github_unavailable",
                message="GitHub API is currently unavailable or timed out.",
                status_code=503,
            ) from last_exc

        raise AppError(
            code="github_unavailable",
            message="GitHub API request could not be completed.",
            status_code=503,
        )

    def _handle_error_response(self, response: httpx.Response) -> None:
        """Map GitHub HTTP error status codes to PR Sentinel AppError exceptions."""
        status_code = response.status_code

        # 404 Not Found
        if status_code == 404:
            raise AppError(
                code="pr_not_found",
                message="GitHub repository or pull request not found.",
                status_code=404,
            )

        # 401 Unauthorized
        if status_code == 401:
            raise AppError(
                code="github_auth_failed",
                message="GitHub authentication failed. Please verify GITHUB_TOKEN.",
                status_code=401,
            )

        # 403 Forbidden / Rate Limited
        if status_code == 403:
            remaining = response.headers.get("x-ratelimit-remaining")
            reset_header = response.headers.get("x-ratelimit-reset")

            # Check if 403 is due to rate limiting
            if remaining == "0" or "rate limit" in response.text.lower():
                reset_msg = ""
                if reset_header:
                    try:
                        reset_epoch = int(reset_header)
                        reset_dt = datetime.datetime.fromtimestamp(
                            reset_epoch, tz=datetime.UTC
                        )
                        reset_msg = (
                            f" Resets at {reset_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}."
                        )
                    except (ValueError, OSError):
                        pass

                auth_advice = (
                    " Configure a valid GITHUB_TOKEN in your environment "
                    "to increase rate limits."
                )
                raise AppError(
                    code="github_rate_limited",
                    message=f"GitHub API rate limit exceeded.{reset_msg}{auth_advice}",
                    status_code=429,
                )

            raise AppError(
                code="github_forbidden",
                message="Access to the requested GitHub resource is forbidden.",
                status_code=403,
            )

        # 5xx Server Errors
        if status_code >= 500:
            raise AppError(
                code="github_unavailable",
                message="GitHub API experienced an outage or server error.",
                status_code=503,
            )

        # Other unexpected errors
        raise AppError(
            code="github_error",
            message=f"GitHub API request failed with HTTP {status_code}.",
            status_code=status_code,
        )

    async def get_pull(self, owner: str, repo: str, number: int) -> PRMetadata:
        """Fetch pull request metadata from GET /repos/{owner}/{repo}/pulls/{number}."""
        path = f"/repos/{owner}/{repo}/pulls/{number}"
        response = await self._request("GET", path)
        return PRMetadata.from_github_api(response.json())

    async def list_pull_files(
        self,
        owner: str,
        repo: str,
        number: int,
        max_files: int = 100,
        total_count: int | None = None,
    ) -> tuple[list[ChangedFile], bool]:
        """Fetch changed files across paginated pages up to max_files.

        Args:
            owner: Repository owner.
            repo: Repository name.
            number: Pull request number.
            max_files: Maximum number of files to retrieve.
            total_count: Total changed files count from PR metadata if known.

        Returns:
            Tuple of (files_list, is_truncated)
        """
        files: list[ChangedFile] = []
        page = 1
        per_page = 100
        truncated = False

        while True:
            path = f"/repos/{owner}/{repo}/pulls/{number}/files"
            response = await self._request(
                "GET",
                path,
                params={"per_page": per_page, "page": page},
            )
            batch = response.json()
            if not isinstance(batch, list) or not batch:
                # No more files available
                break

            remaining_slots = max_files - len(files)

            if len(batch) > remaining_slots:
                # This batch contains more files than needed to reach max_files.
                # Files were actually omitted.
                files.extend(
                    ChangedFile.from_github_api(item)
                    for item in batch[:remaining_slots]
                )
                truncated = True
                break

            # All items in this batch fit within remaining capacity
            files.extend(ChangedFile.from_github_api(item) for item in batch)

            # If fewer items than per_page returned, we reached the final page
            if len(batch) < per_page:
                break

            # If we reached exactly max_files on a full page:
            if len(files) >= max_files:
                if total_count is not None:
                    truncated = total_count > len(files)
                else:
                    # Without total_count, because len(batch) == per_page,
                    # there could be more files on subsequent pages.
                    truncated = True
                break

            page += 1

            # GitHub API has a hard cap of 3,000 files for PRs (30 pages * 100)
            if page > 30 or len(files) >= GITHUB_MAX_FILE_CAP:
                if total_count is not None:
                    truncated = total_count > len(files)
                else:
                    truncated = len(batch) == per_page
                break

        return files[:max_files], truncated

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
        max_bytes: int = 500_000,
    ) -> str | None:
        """Retrieve decoded text content of a file at a specific commit SHA or ref.

        Returns None if:
        - Path contains traversal (..) or is absolute.
        - File is not found or exceeds max_bytes.
        - Target is a directory or non-file object.
        - Content is binary or decoding fails.
        """
        # 1. Path safety validation
        normalized = path.replace("\\", "/").strip()
        parts = [p for p in normalized.split("/") if p]
        if (
            normalized.startswith("/")
            or ".." in parts
            or not normalized
            or normalized.startswith("//")
            or ":" in normalized
            or len(parts) > 30
            or len(normalized) > 300
            or bool(PurePosixPath(normalized).drive)
            or any(
                PurePosixPath(part).stem.upper()
                in {
                    "CON",
                    "PRN",
                    "AUX",
                    "NUL",
                    "COM1",
                    "COM2",
                    "COM3",
                    "COM4",
                    "COM5",
                    "COM6",
                    "COM7",
                    "COM8",
                    "COM9",
                    "LPT1",
                    "LPT2",
                    "LPT3",
                    "LPT4",
                    "LPT5",
                    "LPT6",
                    "LPT7",
                    "LPT8",
                    "LPT9",
                }
                for part in parts
            )
        ):
            logger.warning("Rejected unsafe path in get_file_content: %s", path)
            return None

        # 2. Safely encode each individual path segment
        segments = [quote(seg, safe="") for seg in normalized.split("/") if seg]
        encoded_path = "/".join(segments)

        api_path = f"/repos/{owner}/{repo}/contents/{encoded_path}"
        try:
            response = await self._request("GET", api_path, params={"ref": ref})
        except AppError as exc:
            # If file not found or inaccessible, return None safely
            if exc.code in {"pr_not_found", "github_forbidden"}:
                return None
            raise

        data = response.json()
        if not isinstance(data, dict):
            # Target is a directory list or unexpected response
            return None

        if data.get("type") != "file":
            return None

        size = data.get("size", 0)
        if size > max_bytes:
            logger.info(
                "Skipping file %s exceeding size limit (%d > %d bytes)",
                path,
                size,
                max_bytes,
            )
            return None

        content_encoded = data.get("content")
        if not content_encoded:
            return ""

        encoding = data.get("encoding")
        if encoding != "base64":
            logger.warning("Unsupported file encoding '%s' for %s", encoding, path)
            return None

        try:
            # Base64 decode content (newlines inside base64 string are stripped)
            decoded_bytes = base64.b64decode(content_encoded)
            return decoded_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Failed to decode base64 file content for %s: %s", path, exc)
            return None

    async def fetch_pr_snapshot(
        self,
        parsed: ParsedPR,
        settings: AppSettings | None = None,
    ) -> PRSnapshot:
        """Orchestrate retrieval of PR metadata and changed files snapshot."""
        max_files = settings.max_files if settings is not None else 50

        # Fetch metadata
        metadata = await self.get_pull(parsed.owner, parsed.repo, parsed.number)

        # Retrieve changed files up to max_files
        files, is_truncated = await self.list_pull_files(
            owner=parsed.owner,
            repo=parsed.repo,
            number=parsed.number,
            max_files=max_files,
            total_count=metadata.changed_files,
        )

        # If metadata reports more changed files than retrieved, mark truncated
        if metadata.changed_files > len(files):
            is_truncated = True

        return PRSnapshot(
            metadata=metadata,
            files=files,
            truncated=is_truncated,
        )
