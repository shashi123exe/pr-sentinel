# PR Sentinel — Security Architecture and Policy Guide

This document describes the comprehensive security controls, threat models, containment boundaries, and verification procedures implemented across PR Sentinel.

---

## 1. Threat Model & Security Boundaries

PR Sentinel processes untrusted pull requests from public and private GitHub repositories. Untrusted data sources include:
- Repository files, source code, and diff patches.
- Git commit messages, branch names, and ref names.
- Pull request titles, descriptions, and comments.
- Metadata and webhook payloads received from external network endpoints.

### Primary Threats Addressed:
1. **Server-Side Request Forgery (SSRF)**: Attackers submitting crafted URLs targeting internal network services, cloud metadata endpoints (`169.254.169.254`), or attacker-controlled callback servers.
2. **Arbitrary Code Execution**: Malicious repository files (such as `eslint.config.js`, `pyproject.toml`, or `setup.py`) attempting code execution when linters or analysis engines load configurations.
3. **Path Traversal & Filesystem Destruction**: Adversarial diff paths using directory traversal (`../`), Windows drive letters (`C:`), NTFS Alternate Data Streams (`:$DATA`), Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`), or symlinks to escape temporary workspaces.
4. **Prompt Injection & LLM Jailbreaks**: Untrusted comments or diffs instructing the AI model to ignore safety rules, forge delimiters, output fake findings, or execute external actions.
5. **Denial of Service (DoS)**: Extremely large diffs, minified single-line files, binary blobs, cyclic AST structures, or hanging linter subprocesses designed to exhaust CPU, memory, or disk.
6. **Credential Leakage**: GitHub personal access tokens or Google Gemini API keys appearing in application logs, database records, HTTP error responses, or subprocess environments.
7. **Cross-Site Scripting (XSS)**: Malicious code payloads embedded in PR titles or findings executing in the developer's dashboard browser.
8. **SQL Injection**: Malicious input in API filter parameters modifying database queries.

---

## 2. Server-Side Request Forgery (SSRF) Prevention

### URL Parsing & Whitelist Validation (`app/services/url_parser.py`)
- **Strict Host Whitelist**: Only canonical `https://github.com/{owner}/{repo}/pull/{number}` URLs are accepted.
- **Port Pinning**: Explicit ports (`github.com:8080`, `github.com:443`) are rejected.
- **Userinfo Rejection**: URLs containing embedded credentials (`user:pass@github.com`) are rejected.
- **Scheme Validation**: Only `https://` is permitted. `http://`, `ftp://`, `file://`, and other protocols are blocked.
- **IP Address & Internal Target Blocking**: Numeric IPv4, IPv6, localhost, and private address ranges are rejected.
- **Encoding Attacks**: Percent-encoded characters, CRLF injections, and punycode/unicode lookalikes are blocked.

### Outbound HTTP Destination Pinning (`app/services/github_client.py`)
- **Strict Destination Prefix**: All outbound requests dispatched by `GitHubClient` are validated against `https://api.github.com/`. Any URL attempting to target external or non-GitHub hosts is aborted with a `github_security_violation` error.
- **Redirects Disabled**: HTTP redirects (`301 Moved Permanently`, `302 Found`) are not automatically followed (`follow_redirects=False`). Redirects to cloud metadata services (`http://169.254.169.254/`) or external webhooks are strictly rejected.

---

## 3. Path Traversal & Filesystem Isolation

### Path Containment (`app/analysis/runner.py` & `app/services/github_client.py`)
- **Relative Path Confinement**: Every file path processed during static analysis is normalized and verified using `Path.is_relative_to(temp_dir)`. Any file attempting to write outside the workspace root is rejected.
- **Windows Reserved Device Name Filtering**: Path components matching Windows device drivers (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`) are filtered to prevent OS device driver lockups.
- **NTFS Stream Rejection**: Colons (`:`) in paths are blocked to prevent Alternate Data Stream (`:$DATA`) manipulation.
- **UNC & Drive Path Blocking**: Forward slash (`//`) and backslash (`\\\\`) UNC network shares and drive specifications (`C:`) are rejected.
- **Symlink Protection**: Filesystem symlinks are verified before traversal to prevent sandbox escape.
- **Workspace Cleanup**: Analysis runs in ephemeral directories (`tempfile.TemporaryDirectory`) that are cleaned up upon completion or failure.

---

## 4. Code Execution Prevention & Static Tool Sandboxing

### No Dynamic Evaluation
- The backend contains **zero** instances of `eval()`, `exec()`, `__import__()`, `pickle`, or `shelve`.
- Context retrieval and keyword extraction use Python's built-in `ast` module (`ast.parse`) strictly for static syntax analysis without runtime execution.

### Tool Sandboxing (`app/analysis/`)
- **Isolated CLI Flags**:
  - **Ruff**: Executed with `--isolated` to ignore repository `pyproject.toml` or `ruff.toml`.
  - **ESLint**: Executed with `--no-config-lookup -c <internal_path>` targeting PR Sentinel's audited internal config (`backend/tools/eslint/eslint.config.mjs`).
- **Repository Configuration Exclusion**:
  - Repository configuration files (`eslint.config.js`, `pyproject.toml`, `ruff.toml`, `package.json`, `setup.py`, etc.) are explicitly excluded from being written to the temporary analysis workspace.
- **Subprocess Security**:
  - All tools are dispatched via `asyncio.create_subprocess_exec` with explicit argument lists. `shell=True` is forbidden.
  - Subprocess environments are sanitized via `get_safe_environment()`, stripping all application API keys and tokens.

---

## 5. Prompt Injection Defense & LLM Safety

### XML Envelope Containment (`app/ai/prompts.py`)
- All repository code, commit metadata, and static findings are enclosed in XML-style tags (`<pr_data>`, `<files>`, `<diff>`, `<context>`).
- **Delimiter Neutralization**:
  - Adversarial closing tags (`</diff>`, `</file>`, `</pr_data>`) are neutralized to `&lt;/diff>`, preventing jailbreak escapes.
  - Forged opening tags of reserved envelopes are similarly neutralized.
- **Attribute Escaping**:
  - PR titles, author handles, and commit SHAs are attribute-escaped (`"`, `'`, `&`, `<`, `>`).
- **Strict System Instructions**:
  - Gemini system prompt establishes that all content inside `<pr_data>` is untrusted user data to be analyzed, never obeyed.

### LLM Execution Bounds (`app/ai/gemini_client.py`)
- **Zero Tool Access**: PR Sentinel does not configure, register, or grant function calling / tools to the Gemini model. The model has no access to external systems or APIs.
- **Structured Schema Enforcement**: Output is constrained to Pydantic structured JSON (`AIReviewResponse`) matching predefined schemas. Free-form text and markdown outputs are validated or rejected.

---

## 6. AI Finding Verification & Hallucination Defense

### Verbatim Evidence Verification (`app/services/validation.py`)
- Every finding produced by Gemini is verified by `FindingValidator`:
  - **File Existence**: The referenced `file_path` must exist among the files modified in the PR.
  - **Line Bounds**: Line ranges (`line_start`, `line_end`) must intersect added/modified lines in the diff patch or retrieved context.
  - **Evidence Verification**: The quoted code evidence must match verbatim (or normalized token match) against the actual code. Hallucinated findings are dropped.
  - **Telemetry**: All dropped findings are recorded in `drop_reasons` metrics (`unverifiable_evidence`, `nonexistent_file`, `disabled_category`).

---

## 7. Resource Limits & Denial of Service (DoS) Controls

### Diff & File Limits (`app/services/file_filter.py`)
- **Per-File Diff Limit**: Individual file diffs exceeding 50 KB (`MAX_PER_FILE_DIFF_BYTES`) are skipped (`oversized_diff`).
- **PR Diff Cap**: Cumulative diff size is capped at 500 KB (configurable up to 5 MB hard limit).
- **File Count Cap**: Default limit of 50 files per review (hard maximum of 200).
- **Minified Files**: Files with lines exceeding 1,000 characters or `.min.js`/`.min.css` extensions are skipped (`minified_file`).
- **Binary Files**: Known binary extensions (`.png`, `.exe`, `.dll`, `.zip`, `.pyc`, etc.) and git binary patches are skipped.
- **Lockfiles**: `package-lock.json`, `poetry.lock`, `yarn.lock`, etc., are skipped (`lockfile`).

### Subprocess Timeouts (`app/analysis/runner.py`)
- All static analysis subprocesses (Ruff, Bandit, ESLint) enforce a 30.0-second timeout (`SUBPROCESS_TIMEOUT_SECONDS`).
- Subprocess output buffers are capped at 5 MB (`MAX_OUTPUT_BYTES`) to prevent memory exhaustion.

### Context Limits (`app/context/keyword_retriever.py`)
- Maximum 50 context snippets, 100 KB total retrieved bytes, and 10 files fetched from GitHub.

---

## 8. Secret Redaction & Logging Safety

### Pattern-Based Sanitization (`app/logging_config.py`)
- All log messages, arguments, formatting streams, and exception tracebacks pass through `RedactingFilter` and `RedactingFormatter`.
- Sensitive token formats automatically scrubbed:
  - GitHub Classic PATs: `ghp_[A-Za-z0-9_]{20,}` -> `[REDACTED_GITHUB_TOKEN]`
  - GitHub Fine-Grained PATs: `github_pat_[A-Za-z0-9_]{20,}` -> `[REDACTED_GITHUB_TOKEN]`
  - Google Gemini API Keys: `AIza[0-9A-Za-z\-_]{35}` -> `[REDACTED_GEMINI_KEY]`
  - Bearer Tokens: `Bearer <token>` -> `Bearer [REDACTED_TOKEN]`
  - Authorization Headers: `Authorization: <token>` -> `Authorization: [REDACTED_AUTH]`

### Error Envelope Sanitization (`app/errors.py`)
- HTTP error responses return uniform JSON envelopes (`code`, `message`, `request_id`).
- Raw exception messages, stack traces, and environment variables are never exposed in production HTTP responses.

---

## 9. SQL Injection Prevention & Query Whitelisting

### Parameterized ORM (`app/db/`)
- All database queries are executed using SQLAlchemy 2.0 ORM expressions (`select(Model).where(...)`).
- Raw SQL string formatting or concatenation (`f"SELECT ... {input}"`) is strictly prohibited.

### Input Whitelisting (`app/api/reviews.py`)
- **Sort Field Whitelist**: `sort_by` is validated with regex `^(severity|confidence|file)$`. Any arbitrary SQL injection payload is rejected with HTTP 422.
- **Sort Order Whitelist**: `sort_order` is validated with regex `^(asc|desc)$`.
- **Pagination Safety**: `page` (ge=1) and `page_size` (ge=1, le=100) are constrained against denial of service.

---

## 10. Rate Limiting & Throttling

### SlowAPI Throttling (`app/rate_limit.py`)
- **Review Creation (`POST /api/reviews`)**: Restricted to **5 requests per minute** per client IP.
- **Default Endpoints**: Global rate limit of **60 requests per minute**.
- **Retry-After Header**: When limits are exceeded, the API returns HTTP 429 Too Many Requests with a `Retry-After` header indicating wait duration.

---

## 11. Frontend Cross-Site Scripting (XSS) Protection

### React DOM Safety (`frontend/src/`)
- User-supplied text (PR titles, descriptions, finding explanations, and evidence code) is rendered via standard React JSX expressions (`{text}`), ensuring automatic HTML entity escaping by React DOM.
- No untrusted data is passed to `dangerouslySetInnerHTML`.
- Code diff rendering in `DiffViewer` parses diff lines into plain text elements without DOM script execution.

---

## 12. Security Verification & Test Suite

PR Sentinel includes an automated security test suite under `backend/tests/security/`:
1. `test_ssrf_url_validation.py`: 67 test cases covering lookalike domains, protocols, userinfo, IP addresses, encoding attacks, and respx redirect blocking.
2. `test_path_traversal.py`: 47 test cases covering directory traversal, Windows reserved devices, NTFS streams, UNC paths, and prefix collisions.
3. `test_code_execution_prevention.py`: AST scans confirming zero `eval`/`exec`/`pickle`/`shell=True`, isolated tool flags, and repo config exclusion.
4. `test_prompt_injection.py`: Delimiter neutralization, XML attribute escaping, zero tool declarations, and rejection of fabricated AI findings.
5. `test_resource_limits.py`: Single-file diff caps (50KB), cumulative caps, lockfile/minified exclusions, and subprocess timeout handling.
6. `test_secret_redaction.py`: Direct token regex scrubbing, logging filter, exception traceback scrubbing, and error envelope hygiene.
7. `test_sql_injection.py`: Parameterized queries, sort/order whitelisting, and pagination bounds.
8. `test_rate_limiting.py`: SlowAPI rate limit enforcement (5/min on reviews POST) and Retry-After verification.
9. `frontend/src/test/SecurityXSS.test.tsx`: Vitest tests verifying plain text rendering of `<script>`, `<img onerror=...>`, and `<iframe src=...>` payloads in findings and diffs.

Automated verification script:
```powershell
.\scripts\security-check.ps1
```

### 12.1. Verified Test Suite Accounting & Baseline Reconciliations

| Domain | Phase 9 Baseline | Phase 10 Hardening Additions | Phase 10 Security Additions | Current Total Tests | Pass Rate |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Backend** | 230 | 9 (`test_validation`, `test_dedupe`) | 143 (`tests/security/`) | **382** | **100%** (382/382 passed) |
| **Frontend** | 43 | 0 | 2 (`SecurityXSS.test.tsx`) | **45** | **100%** (45/45 passed) |
| **Combined** | **273** | **9** | **145** | **427** | **100%** (427/427 passed) |

#### Reconciliation of Discrepancies:
- **Baseline Reconciled**: The genuine verified baseline established at Phase 9 closeout was **273 tests** (230 backend + 43 frontend). The earlier draft citation of "238" was an arithmetic conflation taking the 230 backend baseline and adding 8 security test files ($230 + 8 = 238$); that erroneous figure is formally retracted.
- **Security Test Count**: The security suite comprises exactly **143 backend security tests** across 8 modules in `backend/tests/security/` (plus 2 frontend XSS tests). An earlier draft reference to "142 passed" occurred during an interim test run where a single test required an argument fix; upon completion, all 143 backend security tests pass.
- **Complete Backend Count (373 vs 382)**: The complete backend suite comprises **382 tests** (67 integration + 172 unit + 143 security). The figure 373 resulted from naively adding 143 security tests to the 230 Phase 9 baseline, inadvertently omitting the 9 unit tests added during Phase 10 Task 4 validation and deduplication hardening ($230 + 9 + 143 = 382$).

### 12.2. Live Review Verification & Finding Provenance (`pallets/flask#6176`)

- **Official Live Review**: Verified under Review ID `6ee19a69-b95b-46a0-b1b9-6e6ff328b586`.
- **Finding Accounting**: Produced **0 AI findings and 0 static findings** (`findings_summary={"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0}`).
- **Correction of Stale Claims**: Any prior report reference alleging "1 static finding from Ruff" on PR #6176 is **factually erroneous**. Ruff, Bandit, and Gemini produced zero findings on `src/flask/app.py` (+67/-11). The single test finding used to exercise the UI drawer, triage mutations, and cascade deletion was an isolated synthetic fixture injected into a disposable database (`test_browser_isolated.db`) for browser CDP automation.

### 12.3. Dependency Vulnerability Audit Status

- **Status**: **NOT PERFORMED**.
- **Explanation**: `pip-audit` was not installed in the offline Python 3.12 virtual environment. In accordance with strict development rules forbidding unapproved offline package installations, no external vulnerability scanner was installed. Running `pip list --outdated` was performed to review package currency, but is explicitly recorded as a package version check and NOT a dependency vulnerability audit.

---

## 13. Explicit MVP Limitations & Future Hardening Roadmap

### Current Limitations:
1. **Single-User MVP**: PR Sentinel currently operates in local / single-tenant developer mode without multi-tenant authentication, user accounts, or role-based access control (RBAC).
2. **Process-Level Isolation**: Static analysis tools run as isolated OS subprocesses with restricted environments. Full kernel-level sandbox containment (e.g. gVisor, Docker containers, or Firecracker microVMs) is recommended for multi-tenant cloud deployments.
3. **In-Memory Rate Limiting**: SlowAPI uses an in-memory key-value store suitable for single-instance deployments. Distributed deployments require Redis-backed rate limiting.
4. **Python Dependency Vulnerability Auditing**: An automated Python dependency vulnerability audit (e.g. via `pip-audit`) was **NOT PERFORMED** offline because `pip-audit` was not installed in the local virtual environment.
5. **Docker Runtime Security & Isolation Not Locally Verified**: While Dockerfiles and `docker-compose.yml` configure non-root users (`appuser`, `nginx`), `no-new-privileges: true`, and `cap_drop: [ALL]`, live container runtime execution and capability dropping could not be tested locally due to the absence of the Docker daemon on the Windows host.
6. **Hosted CI Execution Not Yet Demonstrated**: While the GitHub Actions workflow (`.github/workflows/ci.yml`) is structurally and syntactically validated with pinned commit SHAs and read-only permissions, live execution has not yet been executed in a hosted GitHub repository runner.

### Recommended Future Hardening:
- Deploy static analysis runners inside isolated, ephemeral container sandboxes (e.g., Docker or OCI containers with dropped capabilities).
- Introduce GitHub App authentication with fine-grained installation tokens and OAuth2/OIDC user authentication.
- Add Webhook signature verification (`X-Hub-Signature-256`) for automated pull request triggers.
- Integrate automated containerized dependency vulnerability auditing (`pip-audit`, `npm audit`) in the CI pipeline.
