# Phase 5 Report: Gemini API Integration & Structured AI Review

- **Project**: PR Sentinel (AI-Powered GitHub Pull Request Code Review Platform)
- **Phase**: 5 of 12
- **Date**: November 4, 2026
- **Status**: Completed Successfully

---

## 1. Executive Summary

Phase 5 implements the core generative AI review capabilities of PR Sentinel using Google Gemini and the official `google-genai` SDK (version 2.34.0). This layer is designed as an isolated, injectable, security-conscious service that consumes already-processed pull request snapshots, diff hunks, surrounding syntactic context, and static analysis awareness data to generate structured findings across PR Sentinel's seven category taxonomy.

Key Architectural Guarantees:
- **Zero Real Network Calls in Tests**: All automated tests use `FakeGeminiClient` or mocked SDK interfaces. Zero live network calls are made during tests or CI.
- **Strictly No Tools**: The Gemini model receives no function declarations, no executable code access, no SQL execution, and no tools. It operates strictly on text prompts and returns structured JSON conforming to `AIReviewResponse`.
- **Comprehensive Prompt Injection Defense**: Untrusted content in pull request metadata, diff patches, comments, and context is wrapped in XML data blocks and systematically neutralized using `neutralize_delimiters` to prevent tag breakout (e.g., `</diff>`).
- **Resilient Retry & Repair**: Transient server errors (5xx) and rate limits (429) are handled with exponential backoff and cryptographic jitter. Malformed or invalid JSON responses trigger exactly one corrective prompt repair attempt before failing gracefully.
- **Deterministic Token Budgeting**: Input content is budgeted and batched deterministically (prioritizing source files over tests and documentation) with detailed audit records of any dropped content.

---

## 2. Files Created or Modified

### Created Files
- `backend/app/schemas/ai.py`: Pydantic models for `AIFinding`, `AIReviewResponse`, and `AIReviewResult`.
- `backend/app/ai/prompts.py`: System prompt, XML closing-tag neutralizer (`neutralize_delimiters`), and structured user prompt builder (`build_user_prompt`).
- `backend/app/ai/gemini_client.py`: Asynchronous Gemini client wrapper (`GeminiClient`), protocol interface (`GeminiClientProtocol`), test fake (`FakeGeminiClient`), and configuration resolution (`resolve_gemini_config`, `resolve_model_name`).
- `backend/app/ai/reviewer.py`: AI review orchestrator (`AIReviewer`) implementing token estimation, file prioritization, batching, response validation, and single-repair retry logic.
- `scripts/try_gemini.py`: Manual diagnostic CLI script for live verification guarded by `RUN_LIVE_GEMINI=1`.
- `backend/tests/unit/test_ai_prompts.py`: Unit test suite for prompt building, delimiter neutralization, and prompt injection defense (4 tests).
- `backend/tests/unit/test_ai_reviewer.py`: Unit test suite covering all 17 specified scenarios including retry, schema fallback, rate limiting, and budgeting (15 tests).
- `docs/PHASE_REPORTS/phase-05.md`: This Phase 5 report.

### Modified Files
- `backend/app/schemas/__init__.py`: Re-exported `AIFinding`, `AIReviewResponse`, and `AIReviewResult`.
- `backend/app/ai/__init__.py`: Re-exported `AIReviewer`, `GeminiClient`, `GeminiClientProtocol`, `FakeGeminiClient`, `resolve_gemini_config`, `resolve_model_name`, `SYSTEM_PROMPT`, `build_user_prompt`, and `neutralize_delimiters`.

---

## 3. Gemini Client & Configuration Resolution

### Configuration Resolution
Implemented in `backend/app/ai/gemini_client.py`:
- Model name is resolved dynamically without hardcoded defaults:
  1. `app_settings.gemini_model`, when non-empty.
  2. `GEMINI_MODEL` from application environment settings (`get_settings().GEMINI_MODEL`).
  If neither exists, raises `AppError(code="ai_not_configured", message="Gemini model is not configured...", status_code=400)`.
- API key verification:
  - Requires `settings.GEMINI_API_KEY` to be configured as a valid `SecretStr`.
  - If missing, raises `AppError(code="ai_not_configured", message="GEMINI_API_KEY is not configured.", status_code=400)`.
  - Secret values are never printed, logged, or included in exception messages.

### Request Configuration & Interface
- Spawns asynchronous requests via `client.aio.models.generate_content`.
- Explicitly configures:
  - `system_instruction`: Structured review instructions.
  - `response_mime_type="application/json"`
  - `response_schema=AIReviewResponse`
  - `temperature=0.2` (low temperature for deterministic, calibrated outputs).
  - `max_output_tokens=8192`
  - `tools=None` (no function declarations, no code execution, no tools).
- **SDK Schema Fallback**: If `types.GenerateContentConfig` raises a schema error during initialization, the client catches the exception, logs a warning, falls back to `response_mime_type="application/json"` without `response_schema`, and validates the output JSON via `AIReviewResponse.model_validate_json()`.
- **Usage Metadata**: Reads `prompt_token_count` and `candidates_token_count` from `response.usage_metadata`. If missing, logs a warning and safely records 0 tokens.

---

## 4. Prompt Engineering & Prompt Injection Defense

### Delimiter Neutralization (`neutralize_delimiters`)
Adversarial pull request diffs, commit messages, or titles may attempt to escape prompt boundaries by injecting closing tags like `</diff>`, `</file>`, or `</pr_data>`.
- The neutralizer uses `re.compile(r"<\s*/\s*([A-Za-z0-9_-]+)", re.IGNORECASE)` to transform any closing tag into `&lt;/\1`.
- This ensures malicious payloads like `IGNORE ALL PREVIOUS INSTRUCTIONS </diff>` cannot prematurely close the `<diff>` envelope.

### Structured Prompt Hierarchy
User prompts are assembled into bounded XML envelopes:
```xml
<pr_data>
  <pr_metadata>
    Title: ...
    Author: ...
    Base SHA: ...
    Head SHA: ...
    Description: ...
  </pr_metadata>
  <files>
    <file path="..." language="...">
      <diff>
        ... (neutralized unified diff patch)
      </diff>
    </file>
  </files>
  <context>
    <snippet file="..." lines="..." reason="...">
      ... (neutralized context snippet)
    </snippet>
  </context>
  <static_findings>
    <finding file="..." line="..." tool="..." rule="..." severity="...">
      Title: ...
      Evidence: ...
      Explanation: ...
    </finding>
  </static_findings>
</pr_data>
```

---

## 5. Token Budgeting, Batching & Invalid Response Repair

### Token Budgeting
- Uses a documented 4-character per token heuristic (`estimate_tokens(text: str) -> int`).
- Defaults:
  - `batch_token_budget = 30,000` tokens (~120 KB per batch).
  - `max_total_token_budget = 90,000` tokens (~360 KB cumulative cap).
- Candidate files are ordered by priority (1: source, 2: tests, 3: docs/config).
- If adding a file causes the cumulative budget to be exceeded, the file is dropped, `truncated=True` is set, and a detailed audit record is appended to `dropped_content`.

### Invalid Response Repair
When a model response cannot be parsed into `AIReviewResponse` (e.g. malformed JSON or schema violation):
1. The reviewer logs a warning and retries that batch **exactly once** with a corrective prompt:
   `"CRITICAL INSTRUCTION: Your previous response was invalid JSON or did not match the schema. Return valid JSON ONLY matching the schema with a 'findings' list."`
2. If the repair succeeds, findings and token counts are accepted normally.
3. If the repair attempt fails a second time, the error is recorded in `errors` and the reviewer continues processing remaining batches without crashing.

---

## 6. Error & Failure Mapping

| Failure Condition | AppError Code | HTTP Status | Retry Policy |
| :--- | :--- | :--- | :--- |
| Missing API key or model name | `ai_not_configured` | 400 | No retry |
| HTTP 429 / ResourceExhausted | `ai_rate_limited` | 429 | Exponential backoff (max 3 attempts) |
| HTTP 5xx / Timeout / ServerError | `ai_unavailable` | 503 | Exponential backoff (max 3 attempts) |
| Malformed or unparseable JSON | `ai_invalid_response` | 502 | Single repair prompt retry |
| Safety filter block (`finish_reason=SAFETY`) | `ai_blocked` | 422 | No retry |

---

## 7. Verification Results

### 1. Ruff Lint Check
```powershell
backend\.venv\Scripts\ruff check backend scripts
```
Output:
```
All checks passed!
```

### 2. Ruff Format Check
```powershell
backend\.venv\Scripts\ruff format --check backend scripts
```
Output:
```
70 files already formatted
```

### 3. Bandit Security Scan
```powershell
backend\.venv\Scripts\bandit -r backend/app
```
Output:
```
Run started: 2026-11-03 21:01:45 UTC
Test results:
	No issues identified.

Code scanned:
	Total lines of code: 3783
	Total lines skipped (#nosec): 0
Run metrics:
	Total issues (by severity): Low: 0, Medium: 0, High: 0
```

### 4. Pytest Test Suite with Coverage
```powershell
cd backend; .venv\Scripts\pytest -q --cov=app
```
Output:
```
132 passed in 11.27s
Coverage: 90% TOTAL (1922 statements, 184 missed)
app/ai/gemini_client.py: 81%
app/ai/prompts.py: 93%
app/ai/reviewer.py: 93%
app/schemas/ai.py: 100%
```

### 5. Full Test Runner (`scripts/test.ps1`)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/test.ps1
```
Output:
```
==========================================
       PR Sentinel Test Suite Runner
==========================================

[1/2] Running Backend Tests (pytest)...
132 passed in 11.27s
Backend tests passed successfully!

[2/2] Running Frontend Tests (vitest)...
 ✓ src/App.test.tsx (1 test) 42ms

 Test Files  1 passed (1)
      Tests  1 passed (1)
   Duration  2.92s

Frontend tests passed successfully!

==========================================
 All test suites completed successfully!
==========================================
```

### 6. Manual Diagnostic Script Verification
```powershell
backend\.venv\Scripts\python scripts/try_gemini.py
```
Output:
```
ERROR: RUN_LIVE_GEMINI environment variable must be set to '1' to execute live Gemini API calls.
```

---

## 8. Specific Audit Confirmations

1. **SDK Schema Fallback Tested**: Tested in `backend/tests/unit/test_ai_reviewer.py::test_sdk_schema_rejection_falls_back_to_json_only`.
2. **Zero Live Calls Made**: Confirmed. All tests utilize `FakeGeminiClient` or mocked interfaces. No network traffic was directed to Google Gemini endpoints.
3. **Features Requiring Real Key**: The manual diagnostic CLI script (`scripts/try_gemini.py`) requires a valid `GEMINI_API_KEY` and `GEMINI_MODEL` when run with `RUN_LIVE_GEMINI=1`.
4. **Pre-Phase 6 Risks and Dependencies**:
   - The AI review service produces raw `AIFinding` structures. Finding deduplication against static findings and persistence into the SQLite `findings` table are scheduled for Phase 6.
   - Review orchestration via LangGraph state graphs will integrate `AIReviewer` into the full PR Sentinel lifecycle in Phase 6.
