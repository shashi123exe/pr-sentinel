# Phase 3 Report: GitHub API Integration & Pull Request Retrieval

- **Project**: PR Sentinel (AI-Powered GitHub Pull Request Code Review Platform)
- **Phase**: 3 of 12
- **Date**: November 4, 2026
- **Status**: Completed Successfully

---

## 1. Executive Summary

Phase 3 implements the GitHub integration layer for PR Sentinel. This layer is responsible for validating incoming pull request URLs, authenticating against the GitHub REST API, safely fetching pull request metadata and changed files across paginated responses, safely decoding file contents, and enforcing strict security boundaries (fixed origin, path traversal prevention, token redaction, and bounded resource limits).

In accordance with Phase 3 specifications:
- All network calls during automated testing are 100% mocked using `respx`; no external network requests are made during tests.
- Review processing remains intentionally deferred (`status: queued`); no background review execution or static analysis was implemented.
- Sensitive authentication tokens (`GITHUB_TOKEN`) are masked and never exposed in logs, API responses, or error messages.

---

## 2. Files Created or Modified

### Created Files
- `backend/app/services/url_parser.py`: Strict GitHub Pull Request URL parser and normalizer (`parse_pr_url`).
- `backend/app/schemas/github.py`: Pydantic schemas (`PRMetadata`, `ChangedFile`, `PRSnapshot`) with safe factory parsers.
- `backend/app/services/github_client.py`: Resilient, asynchronous GitHub REST API client using `httpx.AsyncClient`.
- `backend/tests/unit/test_url_parser.py`: Unit test suite containing 42 test cases verifying strict validation rules.
- `backend/tests/integration/test_github_client.py`: Integration test suite covering all 15 specified scenarios with `respx` mocks.
- `scripts/try_github.py`: Diagnostic CLI script for manual verification against live repositories without leaking secrets.
- `docs/PHASE_REPORTS/phase-03.md`: This Phase 3 report.

### Modified Files
- `backend/app/services/__init__.py`: Re-exported `GitHubClient`, `parse_pr_url`, and `ParsedPR`.
- `backend/app/schemas/__init__.py`: Re-exported `PRMetadata`, `ChangedFile`, and `PRSnapshot`.
- `backend/app/api/reviews.py`: Integrated URL parser into `POST /api/reviews` to validate and normalize URLs, extracting `owner`, `repo`, and `pr_number`.
- `backend/tests/integration/test_reviews.py`: Added test assertion for normalized PR URL and verification that invalid URLs return HTTP 422 with zero database records created.
- `backend/tests/unit/test_lifespan.py`: Applied `@pytest.mark.asyncio` decorator to lifespan recovery test.

---

## 3. Strict PR URL Parser (`backend/app/services/url_parser.py`)

The URL parser enforces strict syntactic, semantic, and security boundaries before any outbound network call or database transaction occurs:

1. **Protocol & Host**:
   - Scheme must be strictly `https`.
   - Host must be exactly `github.com`. Subdomains (`www.github.com`, `api.github.com`), lookalikes (`github.com.attacker.com`), and IP addresses are rejected.
2. **Path Constraints**:
   - Matches regex: `^/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})/pull/([1-9][0-9]{0,6})(?:/(?:files|commits))?/?$`
   - Owner regex: `^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$`
   - Repository regex: `^[A-Za-z0-9._-]{1,100}$` (names `.` and `..` are explicitly rejected).
   - PR Number: Positive integer bounded between `1` and `9,999,999`.
3. **Security Invariants**:
   - Disallows userinfo credentials (e.g. `user@github.com`).
   - Disallows explicit port specifications (e.g. `github.com:443`).
   - Disallows URL query parameters (`?foo=bar`) and URL fragments (`#section`).
   - Disallows whitespace, backslashes, percent-encoded slashes (`%2f`), and punycode.
   - Enforces a maximum length constraint of 300 characters.
4. **Canonical Output**:
   - Normalizes valid URLs into the canonical structure: `https://github.com/{owner}/{repo}/pull/{number}`.
   - Returns a strongly typed `ParsedPR` dataclass containing `owner`, `repo`, `number`, and `canonical_url`.
   - Raises `AppError(code="invalid_pr_url", status_code=422)` with clear, non-leaking error messages on failure.

---

## 4. GitHub Pydantic Schemas (`backend/app/schemas/github.py`)

Data models for representing GitHub pull request payloads safely:

- **`PRMetadata`**: Normalized pull request metadata (`title`, `author`, `base_sha`, `head_sha`, `base_ref`, `head_ref`, `state`, `merged`, `additions`, `deletions`, `changed_files`, `html_url`). The `from_github_api` classmethod safely resolves nested objects (`user`, `base`, `head`) and maps merged states consistently.
- **`ChangedFile`**: Details of changed files (`path`, `status`, `additions`, `deletions`, `patch`, `sha`, `previous_path`, `raw_size`).
- **`PRSnapshot`**: Combined object holding `PRMetadata`, `list[ChangedFile]`, and a `truncated` boolean indicator.

---

## 5. GitHub API Client (`backend/app/services/github_client.py`)

A specialized, asynchronous HTTP client implemented using `httpx.AsyncClient`:

### Resilience & Security Architecture
- **Fixed Origin**: Base URL is hardcoded to `https://api.github.com`. Outbound URLs are constructed exclusively from validated `owner`, `repo`, and `number` segments. Any deviation raises `AppError(code="github_security_violation", status_code=500)`.
- **Timeouts**: Configured with a 5.0s connect timeout and a 20.0s read timeout.
- **Automatic Retries**: Implements exponential backoff on transient errors:
  - Retries on HTTP 502, 503, 504.
  - Retries on network errors and `httpx.TimeoutException`.
  - Non-retryable client errors (401, 403, 404) fail immediately.
- **Error Mapping**:
  - HTTP 404 $\to$ `AppError(code="pr_not_found", status_code=404)`.
  - HTTP 401 $\to$ `AppError(code="github_auth_failed", status_code=401)`.
  - HTTP 403 (with `x-ratelimit-remaining: 0` or rate limit message) $\to$ `AppError(code="github_rate_limited", status_code=429)` including reset time.
  - HTTP 403 (other) $\to$ `AppError(code="github_forbidden", status_code=403)`.
  - HTTP 5xx / timeout exhaustion $\to$ `AppError(code="github_unavailable", status_code=503)`.
- **Safe Pagination & Truncation**:
  - `list_pull_files` iterates GitHub's paginated file endpoints (100 items/page).
  - Enforces `max_files` limit (defaults to setting from `AppSettings`, e.g. 50 files) and marks `truncated = True` if files remain.
  - Enforces GitHub's hard ceiling of 3,000 files / 30 pages.
- **File Retrieval & Path Traversal Prevention**:
  - `get_file_content` verifies that path strings do not contain directory traversal sequences (`..`), leading slashes, or empty segments.
  - Safely URL-quotes path segments.
  - Validates file size against `max_bytes` (skips retrieval if file exceeds threshold, returning `None`).
  - Safely base64-decodes GitHub file content payloads.

---

## 6. Review Creation Endpoint Integration (`backend/app/api/reviews.py`)

The `POST /api/reviews` endpoint was updated to integrate the URL parser:
1. Validates incoming `pr_url` using `parse_pr_url(payload.pr_url)`.
2. Rejects invalid URLs immediately with HTTP 422 `AppError(code="invalid_pr_url")`.
3. Ensures no review, review files, or finding records are created in the database when validation fails.
4. On success, persists the canonical normalized URL along with extracted `owner`, `repo`, and `pr_number`.
5. Returns HTTP 202 Accepted with review status `queued`. Review processing remains deferred.

---

## 7. Manual Diagnostic Script (`scripts/try_github.py`)

A standalone CLI tool designed for developer manual testing:
- Accepts a GitHub PR URL argument (e.g. `python scripts/try_github.py https://github.com/octocat/Hello-World/pull/1`).
- Validates the URL, contacts the GitHub API using configured `GITHUB_TOKEN` (or anonymous mode), and prints formatted PR metadata and file changes.
- Masks tokens and handles exceptions gracefully without stack traces.
- Clearly marked with header comments: `FOR MANUAL TESTING ONLY. NEVER RUN IN CI OR AUTOMATED TESTS.`

---

## 8. Verification & Test Results

### 1. Unit Tests (`backend/tests/unit/test_url_parser.py`)
- **42 test cases** covering:
  - Standard PR URLs and URLs with `/files` or `/commits`.
  - Scheme validation (rejects HTTP, FTP, file, empty).
  - Host validation (rejects subdomains, www, IP addresses, evil lookalikes).
  - Credential and port stripping (rejects `user@github.com`, `:443`).
  - Path traversal and character restrictions (rejects `..`, `%2f`, backslashes, whitespace, tabs, newlines).
  - Owner and repository naming rules (rejects names with punctuation violations or `.` / `..`).
  - PR number boundaries (rejects 0, negative numbers, floats, integers $\ge 10,000,000$).
  - Query parameters and hash fragments.
  - Length limits ($> 300$ chars).

### 2. Integration Tests (`backend/tests/integration/test_github_client.py`)
- **21 test cases** covering all 15 required scenarios and truncation regression invariants with 100% mocked HTTP via `respx`:
  1. Successful PR metadata retrieval (`GET /repos/{owner}/{repo}/pulls/{number}`).
  2. Successful file listing across multiple pages with pagination.
  3. 404 response mapped to `AppError(code="pr_not_found", status_code=404)`.
  4. 403 rate-limit response mapped to `AppError(code="github_rate_limited", status_code=429)` with reset timestamp.
  5. 401 authentication failure mapped to `AppError(code="github_auth_failed", status_code=401)`.
  6. 5xx retry: 502/503 on first attempt, 200 on second; succeeds with 2 calls.
  7. Truncation at `max_files`: returns exactly `max_files` items and `is_truncated = True`.
  8. Base64 file content decoding for `get_file_content`.
  9. Oversized file handling (`size > max_bytes` returns `None`).
  10. Path traversal rejection (`..`, `/absolute`) returns `None` without making outbound requests.
  11. Timeout retry: `httpx.ReadTimeout` on attempt 1, 200 on attempt 2; succeeds. Timeout exhaustion raises HTTP 503.
  12. Correct Pydantic parsing of all metadata and changed file attributes (including `merged` state).
  13. Fixed origin enforcement: calling non-`api.github.com` URLs raises security violation.
  14. Authorization header behavior: absent when no token provided; `Bearer <token>` when provided.
  15. Token confidentiality: verified via `caplog` that token secrets never appear in logs.
  16. `fetch_pr_snapshot` orchestration verifying metadata, file collection, and truncation flag.
  17. Directory and non-base64 content handling returning `None`.
  18. Regression: `snapshot.truncated` is False when exactly `max_files` files exist and all are retrieved.
  19. Regression: `snapshot.truncated` is True when more than `max_files` files exist.
  20. Regression: `snapshot.truncated` is True when PR exceeds GitHub's 3,000-file API cap.

### 3. Overall Test Suite
- Total backend tests: **80 passed**, 0 failed.
- Total backend statement coverage: **93%**.

### 4. Code Quality & Formatting
- `ruff check .`: **All checks passed!** (0 errors).
- `ruff format --check .`: **48 files formatted** (100% compliant).

---

## 9. Next Steps (Phase 4 Preview)

Phase 4 will focus on **Diff Parsing and Static Analysis Ingestion**:
- Parse unified diff patches into structured file hunks, old/new line mappings, and syntax context.
- Implement AST/diff-aware file filters.
- Prepare adapters for static analysis tools (Bandit for Python, ESLint for TypeScript/JavaScript).
