# PR Sentinel — Architecture & System Design Guide

- **Document Version:** 2.0.0
- **Status:** Verified & Production-Deployed
- **Live Deployment:** [https://pr-sentinel-btyl.onrender.com](https://pr-sentinel-btyl.onrender.com)
- **Repository:** [https://github.com/shashi123exe/pr-sentinel](https://github.com/shashi123exe/pr-sentinel)

---

## 1. System Overview

**PR Sentinel** is an automated, high-precision code review platform for GitHub Pull Requests. It bridges deterministic static analysis with generative AI reasoning by orchestrating linters (Ruff, Bandit), AST symbol extractors, and Google Gemini LLMs within an asynchronous, stateful **LangGraph** execution pipeline.

```mermaid
flowchart LR
    Dev["Developer Pull Request"] --> Ingest["1-2. Ingestion & Diff Parsing"]
    Ingest --> Static["3. Parallel AST Static Analysis"]
    Static --> Context["4. Deterministic Symbol Retrieval"]
    Context --> AI["5. Gemini AI Review & Model Fallback"]
    AI --> Val["6. Evidence & Line Verification"]
    Val --> DB[("7. Neon PostgreSQL Persistence")]
    DB --> UI["React 19 Triage Dashboard"]
```

### Core Architectural Principles:
1. **Deterministic Static Foundation**: Fast linters and security scanners execute first via subprocesses in temporary working directories with stripped environments and 30s timeouts, detecting syntax bugs, unused imports, and common CWE vulnerabilities deterministically before AI evaluation.
2. **Deterministic Context (Zero Vector Drift)**: Code context retrieval parses Python ASTs across changed files to extract class and function definitions. This guarantees zero vector embedding latency, zero index drift, fully deterministic context windows, and zero cloud vector database costs.
3. **Structured & Verifiable AI Reasoning**: Google Gemini is constrained to strict Pydantic schemas (`AIReviewResponse`) via the official `google-genai` SDK. Zero tool/function declarations are provided. Every emitted finding must pass strict AST line-boundary checks and verbatim evidence matching against the actual diff patch before acceptance.
4. **Resilient Model Cascade & Quota Protection**: Upstream capacity pressure (HTTP 503) and request timeouts on the primary model (`gemini-3.8-flash`) automatically cascade to `gemini-3.6-flash`. Permanent daily quota exhaustion (RPD) is distinguished from temporary throttling (RPM/TPM) and fails fast without wasteful retries.
5. **Defense-in-Depth Security**: Untrusted repository contents, branch names, and diffs are treated as hostile input: isolated from system command shells, enclosed in neutralizing XML envelopes, inspected within temporary working directories with repository config exclusions and 30s timeouts, and stripped of raw credentials.

---

## 2. End-to-End System Architecture

```mermaid
flowchart TD
    subgraph Client["Developer Interface (React 19 Dashboard)"]
        UI["React SPA (Port 8000 / 8080)"]
        State["React Router & Hooks State"]
        DiffUI["DiffViewer & Inline Annotations"]
        TriageUI["FindingDetailDrawer & Triage Controls"]
        UI --> State
        State --> DiffUI
        State --> TriageUI
    end

    subgraph Service["Unified Production Container (Render Free Tier)"]
        Router["FastAPI REST API Router (/api/*)"]
        SPAMount["StaticFiles Mount (/app/frontend/dist)"]
        RateLimit["SlowAPI Rate Limiter"]
        AuthGate["verify_access_token (sessionStorage gate)"]
        BG["FastAPI BackgroundTasks Queue"]

        Router --> AuthGate --> RateLimit --> BG
    end

    subgraph Graph["LangGraph Review Pipeline (ReviewState)"]
        N1["1. fetch_pr"] -->|OK| N2["2. process_diff"]
        N2 -->|OK| N3["3. static_analysis"]
        N3 -->|OK| N4["4. retrieve_context"]
        N4 -->|OK| N5["5. ai_review"]
        N5 -->|OK| N6["6. validate_findings"]
        N6 -->|OK| N7["7. persist_results"]
        N7 -->|Complete| Terminal["END (Success)"]

        N1 -.->|Failed| FailNode["fail_review"]
        N2 -.->|Failed| FailNode
        N3 -.->|Failed| FailNode
        N4 -.->|Failed| FailNode
        N5 -.->|Failed| FailNode
        N6 -.->|Failed| FailNode
        N7 -.->|Failed| FailNode
        FailNode --> TerminalFail["END (Failed)"]
    end

    subgraph External["External Integrations & Engines"]
        GH["GitHub REST API (v3)"]
        Ruff["Ruff Linter (--isolated)"]
        Bandit["Bandit AST Scanner (-r -q)"]
        PrimaryAI["Gemini 3.8 Flash (Primary)"]
        FallbackAI["Gemini 3.6 Flash (Fallback)"]
    end

    subgraph Persistence["Persistence Layer"]
        ORM["SQLAlchemy 2.0 ORM Engine (Psycopg 3, SSL)"]
        Neon[("Neon Serverless PostgreSQL\n(or SQLite in local dev)")]
        ORM --> Neon
    end

    Client -->|HTTPS + X-Access-Token| Service
    Service --> SPAMount
    BG -->|Execute ainvoke| Graph

    N1 <-->|HTTPS| GH
    N3 -->|subprocess| Ruff
    N3 -->|subprocess| Bandit
    N5 <-->|GenerateContentConfig| PrimaryAI
    N5 -.->|503 / Timeout Fallback| FallbackAI
    N7 -->|Transaction Commit| ORM
    Router <-->|Session Query| ORM
```

---

## 3. Seven-Stage LangGraph Review Pipeline

The review pipeline is modeled as a stateful, compiled `StateGraph` consuming and mutating a strongly typed `ReviewState`:

```mermaid
sequenceDiagram
    autonumber
    actor User as Developer / UI
    participant API as FastAPI Router
    participant BG as Background Task
    participant Graph as LangGraph Engine
    participant GH as GitHub Client
    participant Tools as Static Analyzers
    participant AI as Gemini Client
    participant Val as Finding Validator
    participant DB as Neon PostgreSQL

    User->>API: POST /api/reviews {pr_url}
    API->>DB: Insert Review (status="queued", progress=0%)
    API-->>User: HTTP 202 Accepted {id, status="queued"}
    API->>BG: Dispatch run_review_pipeline(review_id)

    BG->>Graph: compiled_graph.ainvoke(initial_state)

    Note over Graph: Stage 1: fetch_pr (15%)
    Graph->>GH: Validate URL, Fetch PR metadata & raw diffs
    GH-->>Graph: PR metadata, commit SHAs, file patches

    Note over Graph: Stage 2: process_diff (30%)
    Graph->>Graph: Parse unified diffs, filter binaries/lockfiles, calculate hunks

    Note over Graph: Stage 3: static_analysis (50%)
    Graph->>Tools: Dispatch Ruff & Bandit in isolated tempdir
    Tools-->>Graph: Return structured static findings

    Note over Graph: Stage 4: retrieve_context (65%)
    Graph->>Graph: AST extract symbols & fetch surrounding source snippets

    Note over Graph: Stage 5: ai_review (80%)
    Graph->>AI: Send XML-neutralized prompt with diffs & static findings
    alt Primary Model Available
        AI-->>Graph: gemini-3.8-flash returns Pydantic AIReviewResponse
    else Primary 503 Spike / Timeout
        Note over AI: Automatic cascade to gemini-3.6-flash
        AI-->>Graph: gemini-3.6-flash returns Pydantic AIReviewResponse
    end

    Note over Graph: Stage 6: validate_findings (90%)
    Graph->>Val: Verify line ranges, match verbatim evidence, deduplicate
    Val-->>Graph: Validated, deduplicated finding set

    Note over Graph: Stage 7: persist_results (100%)
    Graph->>DB: Save ReviewFiles, Findings, update Review status="completed"

    User->>API: GET /api/reviews/{id} (Polling)
    API->>DB: Query Review & Findings
    DB-->>API: Return complete review snapshot
    API-->>User: HTTP 200 OK (status="completed", findings=[...])
```

### Stage Responsibilities & Failure Policies:

| Stage # | Stage Node | Description | Failure Policy |
| :---: | :--- | :--- | :--- |
| **1** | `fetch_pr` | Parses and validates the GitHub PR URL. Authenticates with GitHub REST API to retrieve PR metadata (title, author, base/head SHAs) and file patches. | Terminal failure: Sets `failed=True`, records error code (`github_not_found`, `github_rate_limited`, `invalid_url`), routes to `fail_review`. |
| **2** | `process_diff` | Parses unified diff patches into structured `ProcessedFile` models. Calculates line counts and hunks. Filters out binaries, lockfiles, minified assets, and files exceeding size caps (50KB/file, 500KB total). | Non-fatal: Unsupported or oversized files are marked `skipped` with audit reasons (`oversized_diff`, `binary_file`, `lockfile`). |
| **3** | `static_analysis` | Writes modified source files to a temporary working directory (`tempfile.TemporaryDirectory`). Executes `ruff` (`--isolated`) and `bandit` (`-r -q`) as subprocesses with stripped environment variables, repository config exclusions, 30s timeouts, and 5MB output caps. | Resilient: Tool execution timeouts or errors are logged in `tool_errors`; pipeline continues safely. |
| **4** | `retrieve_context` | Analyzes Python ASTs of changed files to identify imported classes, functions, and variables. Fetches up to 50 context snippets (100KB total cap) from modified files or referenced repository symbols. | Resilient: Context retrieval failures do not block the review; empty context is passed to the AI stage. |
| **5** | `ai_review` | Constructs structured XML user prompts enclosing diffs, context, and static tool awareness data. Invokes Google Gemini using `google-genai` SDK with strict Pydantic output schemas (`AIReviewResponse`). Cascades to fallback model on demand spikes. | Resilient: If both models fail and static findings exist, workflow continues with warning `"AI review unavailable"`. If no static findings exist, review fails gracefully. |
| **6** | `validate_findings` | Runs all AI-generated findings through `FindingValidator`: verifies that `file_path` exists, confirms line numbers intersect modified diff ranges, and validates that quoted `evidence` matches file content verbatim. Discards unanchored findings and deduplicates overlapping static/AI issues by fingerprint. | Non-fatal: Dropped findings increment `dropped_findings_count` with audit reasons recorded. |
| **7** | `persist_results` | Persists `ReviewFile` records, `Finding` records, token accounting metrics (`prompt_tokens`, `completion_tokens`), and summary aggregations in Neon PostgreSQL via SQLAlchemy ORM. Transitions `Review.status` to `COMPLETED`. | Terminal on DB error: Routes to `fail_review` if commit fails. |
| **-** | `fail_review` | Terminal node triggered whenever any stage flags `state["failed"] = True`. Marks review status as `FAILED`, updates `error_code` and `error_message`, and halts execution cleanly. | Updates database and exits pipeline. |

---

## 4. Gemini AI Model Fallback & Quota Protection

The AI review component (`backend/app/ai/gemini_client.py`) implements a multi-tier resilience architecture:

```mermaid
flowchart TD
    Start["Invoke generate_review()"] --> AttemptPrimary["Attempt 1: gemini-3.8-flash"]

    AttemptPrimary -->|200 OK| Parse["Validate Pydantic Schema"]

    AttemptPrimary -->|401/403 Auth Error| AuthFail["Raise AppError (Immediate Failure, Zero Retries)"]
    AttemptPrimary -->|Permanent Quota (RPD)| QuotaFail["Raise AppError(ai_rate_limited) (Immediate Failure)"]

    AttemptPrimary -->|503 Unavailable or Timeout| RetryPrimary{"Attempt < 3?"}
    RetryPrimary -->|Yes| BackoffPrimary["Exponential Backoff + Jitter (2s, 4s)"] --> AttemptPrimary
    RetryPrimary -->|No| FallbackTrigger["Log Warning: Cascade to gemini-3.6-flash"]

    FallbackTrigger --> AttemptFallback["Attempt 1: gemini-3.6-flash"]
    AttemptFallback -->|200 OK| Parse
    AttemptFallback -->|503 or Timeout| RetryFallback{"Attempt < 3?"}
    RetryFallback -->|Yes| BackoffFallback["Exponential Backoff + Jitter"] --> AttemptFallback
    RetryFallback -->|No| Exhausted["Raise AppError(ai_unavailable, 503)"]

    Parse -->|JSON Schema Valid| Success["Return AIReviewResponse"]
    Parse -->|Invalid JSON| Repair["Single Attempt Repair Prompt"]
    Repair -->|Success| Success
    Repair -->|Fail| Exhausted
```

### Key Safety Mechanisms:
1. **Primary Model**: Pinned to `gemini-3.8-flash` in default configuration.
2. **Fallback Model**: Mapped via `FALLBACK_MODELS = {"gemini-3.8-flash": "gemini-3.6-flash"}`.
3. **Bounded Retries**: At most 3 attempts per model ($\le 6$ total requests), preventing infinite retry loops or quota exhaustion.
4. **Permanent Quota Detection**: Error strings containing `"requests per day"`, `"daily quota"`, or `"check your plan"` are recognized as daily limits (RPD) and fail fast without repeated requests.
5. **Prompt Injection Neutralization**: Closing XML delimiters (`</diff>`, `</file>`) inside untrusted diffs are entity-encoded to `&lt;/diff`.
6. **Zero Tool Calling**: No function declarations or tool configurations are supplied to the LLM, neutralizing tool-calling side effects.

---

## 5. Finding Validation & Deduplication Engine

To filter ungrounded LLM outputs and eliminate redundant alerts, PR Sentinel implements a two-stage verification and deduplication system:

### 1. Verbatim Evidence Verification (`backend/app/services/validation.py`)
Every finding emitted by Gemini is scrutinized by `FindingValidator`:
- **File Confinement**: `file_path` must exist within the pull request file manifest.
- **Diff Intersection**: The line range `[line_start, line_end]` must overlap with modified or added lines in the diff patch.
- **Verbatim Evidence Match**: The quoted `evidence` string must exist verbatim (or token-normalized) within the HEAD file source lines. If the model invents code not present in the repository, the finding is immediately rejected.
- **Telemetry**: Rejected findings increment `dropped_findings_count` with explicit drop reasons (`unverifiable_evidence`, `nonexistent_file`, `disabled_category`).

### 2. Fingerprint Deduplication (`backend/app/services/dedupe.py`)
Static findings and AI findings are merged into a unified set using a deterministic fingerprint algorithm:
$$\text{Fingerprint} = \text{SHA256}(\text{normalized\_path} + ":" + \text{line\_start} + ":" + \text{category} + ":" + \text{normalized\_rule})$$
- When both a static tool (e.g. Bandit) and Gemini flag the same security defect on the same line, the findings are unified into a single record.
- The highest severity and confidence are preserved, and tool sources are attributed transparently (`source="ai"` or `source="static"`).

---

## 6. Database Schema & Persistence Architecture

PR Sentinel uses SQLAlchemy 2.0 ORM with dual-engine support: **Neon Serverless PostgreSQL** via Psycopg 3 in production, and **SQLite** (WAL mode, FK=ON) in local development:

```mermaid
erDiagram
    REVIEWS ||--o{ REVIEW_FILES : "contains"
    REVIEWS ||--o{ FINDINGS : "generates"
    APP_SETTINGS ||--|| REVIEWS : "configures"

    REVIEWS {
        string id PK "UUID4 primary key"
        string pr_url "Validated GitHub PR URL"
        string repository "owner/repo"
        int pr_number "Pull request number"
        string title "PR title (attribute-escaped)"
        string author "GitHub username"
        string base_sha "Base commit hash"
        string head_sha "Head commit hash"
        string status "queued | running | completed | failed"
        string stage "fetch_pr | process_diff | static_analysis | ..."
        int progress_percent "0 to 100"
        string error_code "Nullable error identifier"
        string error_message "Nullable sanitized error message"
        string model_used "gemini-3.8-flash | gemini-3.6-flash"
        int prompt_tokens "Tokens consumed"
        int completion_tokens "Tokens generated"
        json warnings "Diagnostic warnings array"
        datetime created_at "UTC timestamp"
        datetime updated_at "UTC timestamp"
    }

    REVIEW_FILES {
        string id PK "UUID4 primary key"
        string review_id FK "References REVIEWS.id"
        string file_path "Repository relative file path"
        string status "modified | added | deleted"
        int additions "Lines added"
        int deletions "Lines deleted"
        string patch "Unified diff patch content"
        string skipped_reason "Nullable reason for skipping"
    }

    FINDINGS {
        string id PK "UUID4 primary key"
        string review_id FK "References REVIEWS.id"
        string file_path "Repository relative file path"
        int line_start "1-based starting line number"
        int line_end "1-based ending line number"
        string title "Concise finding headline"
        string severity "critical | high | medium | low"
        string category "security | performance | logic_bug | ..."
        string source "ai | static"
        string tool_name "gemini | ruff | bandit"
        string rule_id "Rule code or identifier"
        string evidence "Verbatim source line text"
        string explanation "Detailed description"
        string suggested_fix "Nullable code suggestion"
        float confidence "0.0 to 1.0 confidence score"
        string fingerprint "SHA-256 deduplication hash"
        string triage_status "open | approved | dismissed"
        datetime created_at "UTC timestamp"
    }

    APP_SETTINGS {
        int id PK "Singleton ID = 1"
        string gemini_model "gemini-3.8-flash"
        int max_files "Maximum files to analyze"
        int max_diff_size_kb "Per-file diff cap"
        json enabled_categories "Active taxonomy categories"
        json static_rules_config "Tool rule flags"
        datetime updated_at "UTC timestamp"
    }
```

---

## 7. Cloud Deployment & Container Architecture

PR Sentinel is deployed on Render's Free tier using an Infrastructure-as-Code blueprint ([render.yaml](../render.yaml)) and a unified multi-stage container ([Dockerfile.render](../Dockerfile.render)):

1. **Stage 1 (Frontend Build)**: Node.js 22 Alpine installs production dependencies, compiles TypeScript, and runs Vite build to generate minified SPA static assets (`dist/`).
2. **Stage 2 (Runtime Container)**: Python 3.12-slim installs backend dependencies (FastAPI, Uvicorn, LangGraph, Psycopg 3, Ruff, Bandit), copies the frontend `dist/` directory, and sets unprivileged runtime permissions.
3. **Single-Port Serving (`SERVE_FRONTEND=true`)**: FastAPI mounts the compiled frontend at the root path with client-side SPA fallback to `/index.html` without shadowing `/api/*` or documentation routes.
4. **Proxy Headers & Security**: Uvicorn runs behind Render's edge proxy with `--proxy-headers --forwarded-allow-ips="*"`. Security middleware enforces verified HTTP response headers:
   - `X-Content-Type-Options: nosniff`: Prevents MIME-type sniffing.
   - `X-Frame-Options: DENY`: Prevents clickjacking.
   - `Referrer-Policy: strict-origin-when-cross-origin`: Controls referrer information sent with requests.
5. **Response Compression**:
   - `GZipMiddleware`: Compresses HTTP response payloads exceeding 1000 bytes.
6. **Free-Tier Infrastructure**: Deployed using Render Free Web Service (512MB RAM) and Neon Free PostgreSQL, with Gemini AI review calls operating within Google GenAI free-tier rate limits and quotas subject to provider terms (without implying permanently zero cost or unlimited free usage).
7. **Verified Quality & Test Metrics**:
   - Backend: 433 passed, 1 skipped (93% line coverage across `app`).
   - Frontend: 56 passed across 15 test files (100% pass rate).
   - Combined: 489 passed, 1 skipped.
   - Multi-job CI verification: Frontend, Backend, and Docker Render build all green.
