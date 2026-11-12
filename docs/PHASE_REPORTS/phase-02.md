# Phase 2 Report: FastAPI Backend Foundation, Configuration & Database

- **Project**: PR Sentinel (AI-Powered GitHub Pull Request Code Review Platform)
- **Phase**: 2 of 12
- **Date**: November 4, 2026
- **Status**: Completed Successfully

---

## 1. Files Created or Modified

### Created Files
- `backend/app/config.py` — Pydantic Settings configuration reading from `.env`, managing `GITHUB_TOKEN`, `GEMINI_API_KEY` (SecretStr), `GEMINI_MODEL`, `DATABASE_URL`, `CORS_ORIGINS`, and `LOG_LEVEL` with cached `get_settings()`.
- `backend/app/logging_config.py` — Structured logging configuration, secret redaction filter for GitHub tokens (`ghp_`, `github_pat_`), Google Gemini keys (`AIza...`), and Authorization/Bearer tokens; `RequestIDMiddleware` injecting UUID4 `X-Request-ID`.
- `backend/app/errors.py` — Unified error envelope `{"error":{"code":"...","message":"...","request_id":"..."}}`, custom `AppError`, handlers for `AppError`, Pydantic `RequestValidationError` (422), `HTTPException`, SlowAPI `RateLimitExceeded` (429), and safe generic internal error handler (500).
- `backend/app/db/base.py` — SQLAlchemy 2.0 `DeclarativeBase` root.
- `backend/app/db/models.py` — Typed SQLAlchemy 2.0 models: `Review`, `ReviewFile`, `Finding`, `AppSettings`; Enums: `ReviewStatus`, `Severity`, `FindingStatus`, `Source`, `Category`; check constraints and composite indexes.
- `backend/app/db/session.py` — SQLite engine builder with `check_same_thread=False`, connection event listeners enabling `PRAGMA foreign_keys=ON` and `PRAGMA journal_mode=WAL`, `get_db` generator, and idempotent default `AppSettings` seeding.
- `backend/app/schemas/common.py` — Generic `Page[T]` schema using Python 3.12 PEP 695 generic syntax.
- `backend/app/schemas/settings.py` — Pydantic v2 schemas: `SettingsOut`, `SettingsUpdate` with boundary and type validators.
- `backend/app/schemas/review.py` — Pydantic v2 schemas: `ReviewCreate`, `ReviewCreateResponse`, `ReviewListItem`, `ReviewOut`.
- `backend/app/rate_limit.py` — SlowAPI rate limiter setup keyed by client remote address (default 60/min, 5/min on reviews).
- `backend/app/api/health.py` — `GET /api/health` performing live database connectivity check (`SELECT 1`), returning 503 if unavailable.
- `backend/app/api/settings.py` — `GET /api/settings` and `PUT /api/settings` for singleton configuration management.
- `backend/app/api/reviews.py` — `POST /api/reviews` (returns 202 queued, review processing intentionally deferred), `GET /api/reviews` (paginated), `GET /api/reviews/{id}`, `DELETE /api/reviews/{id}` (cascading children).
- `backend/app/main.py` — FastAPI application factory `create_app()`, CORS middleware, SlowAPI state attachment, and lifespan managing DB schema initialization and recovering interrupted `running` reviews to `failed` (`server_restart`).
- `backend/tests/conftest.py` — Isolated temporary SQLite test database fixtures and `TestClient` override.
- `backend/tests/unit/test_logging.py` — Unit test verifying redaction of GitHub PATs, Gemini API keys, and Bearer headers.
- `backend/tests/unit/test_lifespan.py` — Unit test verifying server restart recovery marks running reviews as failed.
- `backend/tests/integration/test_health.py` — Integration test for `/api/health` success and 503 error handling.
- `backend/tests/integration/test_settings.py` — Integration test for settings defaults, updates, and 422 validations.
- `backend/tests/integration/test_reviews.py` — Integration test for review creation (202), listing, pagination, 404 envelope, and cascade deletion.
- `backend/tests/integration/test_rate_limit.py` — Integration test verifying 6th POST within 1 min returns 429 with `Retry-After`.
- `docs/PHASE_REPORTS/phase-02.md` — This Phase 2 report.

### Modified Files
- `backend/app/db/__init__.py` — Exported models, session, and engine utilities.
- `backend/app/schemas/__init__.py` — Exported schemas.
- `backend/app/api/__init__.py` — Wired routers into `api_router` (`/api`).
- `backend/pyproject.toml` — Added test filterwarnings for Starlette deprecation warnings.
- `scripts/dev.ps1` — Updated backend entrypoint reference to `app.main:app`.

---

## 2. Database Models and Constraints Implemented

### Models (`backend/app/db/models.py`)
1. **`Review`**
   - Primary Key: `id` (String 36 UUID)
   - Columns: `pr_url`, `owner`, `repo`, `pr_number`, `pr_title`, `pr_author`, `base_sha`, `head_sha`, `status` (`ReviewStatus` enum), `current_stage`, `error_code`, `error_message`, `files_changed`, `additions`, `deletions`, `truncated`, `llm_model`, `llm_tokens_in`, `llm_tokens_out`, `created_at`, `completed_at`.
   - Relationships: `files` (`ReviewFile`, cascade="all, delete-orphan"), `findings` (`Finding`, cascade="all, delete-orphan").
   - Indexes: `ix_reviews_created_at`, `ix_reviews_owner_repo_pr_number`, `ix_reviews_status`.
2. **`ReviewFile`**
   - Primary Key: `id` (Integer autoincrement)
   - Columns: `review_id` (ForeignKey `reviews.id`, ondelete="CASCADE"), `path`, `status`, `additions`, `deletions`, `patch`, `language`, `skipped_reason`.
   - Indexes: `ix_review_files_review_id`, UniqueConstraint `uq_review_files_review_id_path(review_id, path)`.
3. **`Finding`**
   - Primary Key: `id` (Integer autoincrement)
   - Columns: `review_id` (ForeignKey `reviews.id`, ondelete="CASCADE"), `file_path`, `line_start`, `line_end`, `title`, `severity` (`Severity` enum), `category` (`Category` enum), `source` (`Source` enum), `tool_name`, `rule_id`, `evidence`, `explanation`, `suggested_fix`, `confidence` (Float), `fingerprint`, `status` (`FindingStatus` enum), `created_at`, `updated_at`.
   - Constraints: `CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_findings_confidence_range")`.
   - Indexes: `ix_findings_review_id_severity`, `ix_findings_review_id_file_path`, UniqueConstraint `uq_findings_review_id_fingerprint(review_id, fingerprint)`.
4. **`AppSettings`**
   - Primary Key: `id` (Integer, singleton id=1)
   - Columns: `max_files` (default 50), `max_diff_bytes` (default 500000), `min_confidence` (default 0.5), `enabled_categories` (JSON list of all categories), `gemini_model` (default "").

### SQLite Features Enabled
- `PRAGMA foreign_keys=ON` verified via connection listener.
- `PRAGMA journal_mode=WAL` verified via connection listener.
- `check_same_thread=False` configured.

---

## 3. API Endpoints Implemented

| Method | Endpoint | Description | Status Code |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/health` | Service and live database connectivity check (`SELECT 1`) | 200 OK / 503 |
| `GET` | `/api/settings` | Retrieve singleton analysis configuration | 200 OK |
| `PUT` | `/api/settings` | Update validated analysis configuration | 200 OK |
| `POST` | `/api/reviews` | Submit PR review request (rate-limited 5/min, deferred processing) | 202 Accepted |
| `GET` | `/api/reviews` | Paginated review listings, newest first | 200 OK |
| `GET` | `/api/reviews/{id}` | Detailed review entity by UUID | 200 OK / 404 |
| `DELETE`| `/api/reviews/{id}`| Delete review with cascade deletion of files & findings | 204 No Content |

---

## 4. Configuration and Security Behavior

- **Secrets Handling**: `GITHUB_TOKEN` and `GEMINI_API_KEY` are typed as `pydantic.SecretStr`, preventing raw exposure in string serialization, representation, or error dumps.
- **Log Redaction**: Regular expressions in `RedactingFilter` automatically mask GitHub classic PATs (`ghp_`), fine-grained PATs (`github_pat_`), Google Gemini API keys (`AIza...`), and `Bearer` authorization headers.
- **Error Envelope**: All error responses consistently use `{"error":{"code":"...","message":"...","request_id":"..."}}`. Unhandled 500 exceptions log tracebacks internally while returning a safe generic message without SQL or stack trace leakage.
- **Request Tracing**: `RequestIDMiddleware` generates a UUID4 for every request and injects it into context, logs, error payloads, and the `X-Request-ID` header.
- **Rate Limiting**: SlowAPI limits `/api/reviews` to 5 POST requests/min per IP, returning 429 and `Retry-After` header.

---

## 5. Pytest & Coverage Results

Command executed:
```powershell
pytest -q --cov=app
```

Output:
```
17 passed, 2 warnings in 3.18s
```

Coverage summary:
| Module | Statements | Missing | Coverage |
| :--- | :--- | :--- | :--- |
| `app/api/__init__.py` | 9 | 0 | 100% |
| `app/api/health.py` | 17 | 0 | 100% |
| `app/api/reviews.py` | 48 | 2 | 96% |
| `app/api/settings.py` | 42 | 4 | 90% |
| `app/config.py` | 23 | 1 | 96% |
| `app/db/__init__.py` | 4 | 0 | 100% |
| `app/db/base.py` | 3 | 0 | 100% |
| `app/db/models.py` | 103 | 0 | 100% |
| `app/db/session.py` | 49 | 8 | 84% |
| `app/errors.py` | 61 | 8 | 87% |
| `app/logging_config.py` | 55 | 7 | 87% |
| `app/main.py` | 44 | 0 | 100% |
| `app/rate_limit.py` | 3 | 0 | 100% |
| `app/schemas/__init__.py` | 4 | 0 | 100% |
| `app/schemas/common.py` | 7 | 0 | 100% |
| `app/schemas/review.py` | 56 | 0 | 100% |
| `app/schemas/settings.py` | 17 | 0 | 100% |
| **TOTAL** | **545** | **30** | **94%** |

---

## 6. Ruff Results

Commands executed:
```powershell
ruff check .
ruff format --check .
```
Result: **All checks passed! 35 files clean, 0 errors, 0 warnings.**

---

## 7. Manual Live Server Verification Results

Command executed:
```powershell
uvicorn app.main:app --port 8000
```
Live probe responses captured:
- `GET http://localhost:8000/api/health` -> HTTP 200, Body: `{"status":"ok","database":"ok"}`, Header: `X-Request-ID: 4b265ae5-52f2-4226-bacf-c26aacf8e2ac`
- `GET http://localhost:8000/docs` -> HTTP 200, Content-Type: `text/html; charset=utf-8` (Swagger UI HTML rendered)

---

## 8. Confirmation of Deferred Processing & Scope Adherence

- In accordance with Phase 2 requirements, `POST /api/reviews` only creates a database record with `status="queued"` and returns HTTP 202.
- A clearly named TODO hook (`# TODO (Phase 6): Dispatch background LangGraph review processing workflow`) is in place.
- No GitHub API calls, PR URL parsing, diff analysis, Gemini calls, LangGraph workflows, or frontend dashboard logic were implemented.

---

## 9. Risks or Dependencies to Consider Before Phase 3

1. **GitHub URL Complexity**: Phase 3 will introduce GitHub URL validation and API integration. Rate limits on GitHub API without a token (60 requests/hr) mean developers must supply a `GITHUB_TOKEN` for robust testing against public and private repos.
2. **Schema Migrations**: Schema creation is currently handled via `Base.metadata.create_all()`. When Alembic migrations are introduced in later phases, existing SQLite tables will need to be version-tracked.
