# PR Sentinel — Architecture & System Design Guide

- **Document Version:** 1.0.0
- **Phase:** 12 (Final System Architecture)
- **Status:** Approved & Verified Implementation

---

## 1. System Overview

**PR Sentinel** is an AI-powered GitHub Pull Request code review platform designed to deliver automated, high-precision, actionable code reviews. It bridges deterministic static analysis with generative AI reasoning by orchestrating linters (Ruff, Bandit, ESLint) and Google Gemini LLMs within an asynchronous, stateful **LangGraph** pipeline.

### Core Architectural Principles:
1. **Deterministic Static Foundation**: Linters and security scanners run first, catching syntax bugs, unused imports, and common vulnerabilities deterministically before AI evaluation.
2. **Deterministic Context (No Vector RAG)**: Code context retrieval uses AST keyword and symbol matching across changed files. This guarantees zero vector embedding latency, zero index drift, fully deterministic context windows, and low operational overhead.
3. **Structured & Verifiable AI Output**: Google Gemini is constrained to Pydantic structured schemas (`AIReviewResponse`). Zero tool/function declarations are provided to the model. Every emitted finding must pass strict AST line-boundary checks and verbatim evidence matching against the actual diff patch before acceptance.
4. **Defense-in-Depth Security**: Untrusted repository contents, branch names, and diffs are treated as hostile input. They are isolated from system command shells, enclosed in neutralizing XML envelopes, and inspected within ephemeral workspaces.
5. **Separation of Concerns**: A FastAPI backend exposes a clean REST API and manages review lifecycles; a modern React 19 dashboard provides real-time polling, inline diff inspection, and finding triage.

---

## 2. End-to-End System Architecture

The following diagram illustrates the complete architectural topology, data boundaries, and component interactions:

```mermaid
flowchart TD
    subgraph Client["Developer Interface (React 19 Dashboard)"]
        UI["React SPA (Port 8080)"]
        State["Zustand / Hooks State"]
        DiffUI["DiffViewer & Inline Annotations"]
        TriageUI["FindingDetailDrawer & Triage Controls"]
        UI --> State
        State --> DiffUI
        State --> TriageUI
    end

    subgraph ReverseProxy["Production Edge"]
        Nginx["Nginx Reverse Proxy (/api -> backend:8000)"]
    end

    subgraph Backend["FastAPI Backend Service (Port 8000)"]
        Router["FastAPI REST API Router (/api)"]
        RateLimit["SlowAPI Rate Limiter (5 rev/min, 60 req/min)"]
        BG["FastAPI BackgroundTasks Queue"]

        Router --> RateLimit
        RateLimit --> BG
    end

    subgraph Graph["LangGraph Review Pipeline (StateGraph)"]
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

    subgraph External["External Services & Analysis Engines"]
        GH["GitHub REST API (v3)"]
        Ruff["Ruff Linter (--isolated)"]
        Bandit["Bandit AST Scanner (-r -q)"]
        ESLint["ESLint Runner (--no-config-lookup)"]
        Gemini["Google Gemini API (google-genai SDK)"]
    end

    subgraph Persistence["Local Persistence Layer"]
        ORM["SQLAlchemy 2.0 ORM Engine"]
        SQLite[("SQLite Database (WAL Mode, FK=ON)\npr_sentinel.db")]
        ORM --> SQLite
    end

    %% Interactions
    UI -->|HTTP /api| Nginx
    Nginx -->|Proxy HTTP 8000| Router
    BG -->|Execute ainvoke| Graph

    N1 <-->|Async HTTPS /repos/.../pulls| GH
    N3 -->|create_subprocess_exec| Ruff
    N3 -->|create_subprocess_exec| Bandit
    N3 -->|create_subprocess_exec| ESLint
    N4 <-->|AST Symbol Extraction| N2
    N5 <-->|JSON Structured Schema| Gemini
    N6 <-->|Verbatim Patch Match & Dedupe| N3
    N7 -->|Transaction Commit| ORM
    Router <-->|Session Query| ORM
```

---

## 3. LangGraph Review Pipeline Stages

The review pipeline is modeled as a stateful, compiled `StateGraph` consuming and updating a typed `ReviewState`. Each stage represents a discrete, fault-tolerant processing step:

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
    participant DB as SQLite DB

    User->>API: POST /api/reviews {pr_url}
    API->>DB: Insert Review (status="queued", progress=0%)
    API-->>User: HTTP 202 Accepted {id, status="queued"}
    API->>BG: Dispatch run_review_pipeline(review_id)

    BG->>Graph: compiled_graph.ainvoke(initial_state)

    %% Stage 1
    Note over Graph: Stage 1: fetch_pr (15%)
    Graph->>GH: Validate URL, Fetch PR metadata & files
    GH-->>Graph: PR metadata, commit SHAs, file patches

    %% Stage 2
    Note over Graph: Stage 2: process_diff (30%)
    Graph->>Graph: Parse unified diffs, filter binaries/lockfiles, calculate hunks

    %% Stage 3
    Note over Graph: Stage 3: static_analysis (50%)
    Graph->>Tools: Dispatch Ruff & Bandit in isolated tempdir
    Tools-->>Graph: Return structured static findings

    %% Stage 4
    Note over Graph: Stage 4: retrieve_context (65%)
    Graph->>Graph: AST extract symbols & fetch surrounding source snippets

    %% Stage 5
    Note over Graph: Stage 5: ai_review (80%)
    Graph->>AI: Send XML-neutralized prompt with diffs & static findings
    AI-->>Graph: Return Pydantic AIReviewResponse

    %% Stage 6
    Note over Graph: Stage 6: validate_findings (90%)
    Graph->>Val: Verify line ranges, match verbatim evidence, deduplicate
    Val-->>Graph: Validated, deduplicated finding set

    %% Stage 7
    Note over Graph: Stage 7: persist_results (100%)
    Graph->>DB: Save ReviewFiles, Findings, update Review status="completed"

    User->>API: GET /api/reviews/{id} (Polling)
    API->>DB: Query Review & Findings
    DB-->>API: Return complete review snapshot
    API-->>User: HTTP 200 OK (status="completed", findings=[...])
```

### Detailed Pipeline Stage Responsibilities:

| Stage # | Stage Node | Description | Failure Policy |
| :---: | :--- | :--- | :--- |
| **1** | `fetch_pr` | Parses and validates the GitHub PR URL. Authenticates with GitHub REST API to retrieve PR metadata (title, author, base/head SHAs) and modified file patches. | Terminal failure: Sets `failed=True`, records error code (`github_not_found`, `github_rate_limited`, `invalid_url`), routes to `fail_review`. |
| **2** | `process_diff` | Parses unified diff patches into structured `ProcessedFile` models. Calculates line addition/deletion counts and hunks. Filters out binary files, lockfiles, minified assets, and files exceeding size limits (50KB per-file cap, 500KB cumulative cap). | Non-fatal: Unsupported or oversized files are marked `skipped` with audit reasons (`oversized_diff`, `binary_file`, `lockfile`). |
| **3** | `static_analysis` | Writes modified source files to an ephemeral temporary directory (`tempfile.TemporaryDirectory`). Executes `ruff` (`--isolated`) and `bandit` (`-r -q`) as unprivileged subprocesses with 30s timeouts. Parses tool JSON outputs into `StaticFinding` models anchored to added lines. | Resilient: Tool execution timeouts or errors are logged in `tool_errors`; pipeline continues safely. |
| **4** | `retrieve_context` | Analyzes Python ASTs of changed files to identify imported classes, functions, and variables. Fetches up to 50 context snippets (100KB total cap) from modified files or referenced repository symbols to provide surrounding semantic scope. | Resilient: Context retrieval failures do not block the review; empty context is passed to the AI stage. |
| **5** | `ai_review` | Constructs structured XML user prompts enclosing diffs, context, and static tool awareness data. Invokes Google Gemini using `google-genai` SDK with strict Pydantic output schemas (`AIReviewResponse`). Applies single-repair retry on malformed JSON. | Resilient: Missing API key or API outages trigger fallback to static analysis findings, recording telemetry in `warnings`. |
| **6** | `validate_findings` | Runs all AI-generated findings through `FindingValidator`: verifies that `file_path` exists in the PR, confirms line numbers intersect modified diff ranges, and validates that quoted `evidence` matches file content verbatim. Drops hallucinated findings and deduplicates overlapping static/AI issues by fingerprint. | Non-fatal: Dropped findings increment `dropped_findings_count` with audit reasons recorded. |
| **7** | `persist_results` | Persists `ReviewFile` records, `Finding` records, token accounting metrics (`prompt_tokens`, `completion_tokens`), and summary aggregations in SQLite via SQLAlchemy ORM. Transitions `Review.status` to `COMPLETED` and sets `progress_percent=100`. | Terminal on DB error: Routes to `fail_review` if commit fails. |
| **-** | `fail_review` | Terminal node triggered whenever any stage flags `state["failed"] = True`. Marks review status as `FAILED`, updates `error_code` and `error_message`, and halts execution cleanly. | Updates database and exits pipeline. |

---

## 4. State Model & Data Contract

All data moving through the review pipeline is encapsulated in the typed `ReviewState` dictionary:

```python
class ReviewState(TypedDict, total=False):
    review_id: str                      # Unique UUID for the review execution
    pr_url: str                         # Verified canonical GitHub pull request URL
    parsed_pr: dict[str, Any] | None    # Owner, repo, and PR number
    pr_meta: dict[str, Any] | None      # PR title, author, commit SHAs, base/head refs
    changed_files: list[dict[str, Any]] # Raw file patch data from GitHub API
    processed_files: list[dict[str, Any]] # Parsed and filtered ProcessedFile objects
    hunks: list[dict[str, Any]]         # Structured diff hunks with line mappings
    static_findings: list[dict[str, Any]] # Normalised StaticFinding objects
    tool_errors: list[str]              # Non-fatal warnings from linters/scanners
    context_snippets: list[dict[str, Any]] # AST-retrieved code symbol snippets
    raw_ai_findings: list[dict[str, Any]]  # Unvalidated AIFinding models from Gemini
    validated_findings: list[dict[str, Any]] # Filtered, verified, deduplicated findings
    errors: list[dict[str, Any]]        # Error logs with stage attribution
    token_usage: dict[str, Any]         # Prompt tokens, completion tokens, model name
    truncation: dict[str, Any]          # Truncation flags and drop reasons
    warnings: list[str]                 # Diagnostic pipeline warnings
    dropped_findings_count: int         # Total count of hallucinated/invalid findings dropped
    failed: bool                        # Boolean flag routing conditionally to fail_review
```

---

## 5. Finding Validation & Deduplication Engine

To eliminate LLM hallucinations and redundant alerts, PR Sentinel implements a two-stage verification and deduplication system:

### 1. Verbatim Evidence Verification (`app/services/validation.py`)
Every finding emitted by Gemini is scrutinized by `FindingValidator`:
- **File Confinement**: Finding `file_path` must exist within the pull request file manifest.
- **Diff Intersection**: The finding line range `[line_start, line_end]` must overlap with modified or added lines in the diff patch.
- **Verbatim Evidence Match**: The quoted `evidence` string must exist verbatim (or token-normalized) within the HEAD file source lines. If the model invents code not present in the repository, the finding is immediately rejected.
- **Telemetry**: Rejected findings increment `dropped_findings_count` with explicit drop reasons (`unverifiable_evidence`, `nonexistent_file`, `disabled_category`).

### 2. Fingerprint Deduplication (`app/services/dedupe.py`)
Static findings and AI findings are merged into a unified set using a deterministic fingerprint algorithm:
$$\text{Fingerprint} = \text{SHA256}(\text{normalized\_path} + ":" + \text{line\_start} + ":" + \text{category} + ":" + \text{normalized\_rule})$$
- When both a static tool (e.g. Bandit) and Gemini flag the same security defect on the same line, the findings are unified into a single record.
- The highest severity and confidence are preserved, and tool sources are attributed transparently (`source="ai"` or `source="static"`).

---

## 6. Database Schema & Persistence Architecture

PR Sentinel stores all state locally in SQLite using SQLAlchemy 2.0 ORM with WAL (Write-Ahead Logging) and foreign key enforcement enabled:

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
        string model_used "e.g. gemini-3.5-flash"
        int prompt_tokens "Input tokens consumed"
        int completion_tokens "Output tokens consumed"
        int dropped_findings_count "Hallucinated findings dropped"
        datetime created_at "UTC timestamp"
        datetime completed_at "UTC timestamp"
    }

    REVIEW_FILES {
        int id PK "Auto-increment ID"
        string review_id FK "References reviews.id (CASCADE)"
        string path "Relative file path"
        string language "python | typescript | javascript | etc."
        string status "added | modified | deleted"
        int additions "Lines added"
        int deletions "Lines removed"
        string skipped_reason "oversized_diff | binary_file | lockfile"
        string patch "Unified diff text"
    }

    FINDINGS {
        int id PK "Auto-increment ID"
        string review_id FK "References reviews.id (CASCADE)"
        string file_path "Relative path to target file"
        int line_start "Starting line number"
        int line_end "Ending line number"
        string title "Concise finding headline"
        string severity "critical | high | medium | low"
        string category "security | bug_risk | performance | style"
        string source "ai | static"
        string tool_name "gemini | ruff | bandit | eslint"
        string rule_id "Rule or vulnerability identifier"
        string evidence "Verbatim quoted code snippet"
        string explanation "Detailed architectural explanation"
        string suggested_fix "Concrete remediation patch"
        float confidence "0.0 to 1.0 confidence score"
        string fingerprint "Deterministic deduplication hash"
        string status "open | approved | dismissed"
        datetime created_at "UTC timestamp"
        datetime updated_at "UTC timestamp"
    }

    APP_SETTINGS {
        int id PK "Singleton row (id=1)"
        int max_files "Maximum files reviewed per PR (default 50)"
        int max_diff_bytes "Maximum total diff bytes (default 500KB)"
        float min_confidence "Confidence threshold (default 0.5)"
        json enabled_categories "Array of active category strings"
        string gemini_model "Dynamic model override"
    }
```

---

## 7. Frontend SPA Architecture

The frontend is a single-page React 19 application built with TypeScript, Vite, Tailwind CSS, and Lucide React icons.

### Key Architectural Modules:
- **`AppRouter` (`src/App.tsx`)**: Configures client-side SPA routing (`/`, `/new`, `/history`, `/settings`, `/reviews/:reviewId`).
- **`DiffViewer` (`src/components/diff/DiffViewer.tsx`)**: Parses unified diff text into syntax-highlighted hunks, computing line numbers and embedding clickable inline gutter badges that link directly to findings.
- **`FindingDetailDrawer` (`src/components/findings/FindingDetailDrawer.tsx`)**: Slide-out drawer presenting finding metadata, verbatim code evidence, formatted explanations, suggested fixes, and triage controls (`open` &rarr; `approved` / `dismissed`).
- **`usePolling` Hook (`src/hooks/usePolling.ts`)**: Adaptive client polling engine (1s intervals during active stages) featuring exponential backoff, connection outage grace period banners, and automatic recovery upon server restarts.
- **`ApiClient` (`src/api/client.ts`)**: Strongly-typed HTTP wrapper communicating over relative `/api` paths. Compatible with both Vite development proxying and production Nginx reverse proxying.

---

## 8. Failure Recovery & Lifecycle Resiliency

PR Sentinel enforces automated recovery from infrastructure failures:
1. **Server Restart Recovery (`app/main.py::lifespan`)**: During application startup, a database scan identifies any reviews trapped in `running` status due to a previous ungraceful shutdown or crash. These stale reviews are automatically transitioned to `failed` with error code `server_restart` and an informative message.
2. **Frontend Polling Resilience**: If the backend restarts while the browser is polling an active review, the frontend displays an outage recovery banner, retains current UI state without blanking, and seamlessly reconnects once the backend listener resumes.
3. **Database Cascade Deletion**: Deleting a review via `DELETE /api/reviews/{id}` cascades cleanly to all associated `review_files` and `findings` records within a single database transaction.
