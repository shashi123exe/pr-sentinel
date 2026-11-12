# PR Sentinel — Technical Interview Guide & Deep Dive

This document is prepared for senior engineering interviews, system design discussions, and architectural walkthroughs of PR Sentinel. It provides honest, technically accurate explanations of design trade-offs, engineering decisions, edge-case defenses, and future scaling bottlenecks.

---

## 1. High-Level Introductions

### The 30-Second Elevator Pitch
> *"PR Sentinel is an asynchronous code-review platform that pairs deterministic static analysis with generative AI to review GitHub pull requests. Instead of treating code review as an unstructured chatbot prompt, PR Sentinel runs linters and security scanners first, gathers syntactic symbol context, and orchestrates Google Gemini inside a compiled LangGraph state machine. Every finding is enforced against Pydantic schemas, validated against verbatim diff evidence, and deduplicated before reaching a modern React dashboard. It solves the two biggest problems with AI code reviews: hallucinations and alert fatigue."*

### The Two-Minute Technical Walkthrough
> *"When a developer submits a GitHub pull request URL, PR Sentinel validates and parses the URL to prevent SSRF, creates a review record in SQLite, and dispatches a background task running a LangGraph workflow.*
>
> *First, it fetches the PR metadata and unified diff from GitHub's REST API. It filters out binaries, lockfiles, and minified code, adhering to strict size caps (50KB per file, 500KB total). Next, in an isolated temporary directory, it executes Ruff and Bandit against modified files using safe, non-shell subprocesses with `--isolated` flags, ensuring untrusted repository configs like `pyproject.toml` never execute host code.*
>
> *Next, an AST-based keyword and symbol retriever extracts definitions of changed classes and functions from the repo to provide semantic scope without the operational complexity or vector drift of an embedding database.*
>
> *The diffs, context snippets, and static findings are wrapped in XML tags with adversarial delimiters neutralized to prevent prompt injection. We invoke Google Gemini through the `google-genai` SDK, constraining output to Pydantic structured JSON. Crucially, the model has zero tool or shell access.*
>
> *Before findings are saved, our validator checks that the referenced file exists, the line range intersects modified lines, and the quoted code evidence matches the file content verbatim. Non-matching findings are dropped with telemetry. Static and AI findings are then deduplicated via SHA-256 fingerprints, persisted in SQLite in WAL mode, and streamed to a React 19 single-page dashboard via polling with network-outage resilience.*
>
> *The entire system is containerized with multi-stage Dockerfiles and Docker Compose 2.24.0+, and tested by a 427-test automated suite (382 backend, 45 frontend) including 143 dedicated security tests."*

---

## 2. Deep-Dive Architectural Questions & Answers

### 1. What problem does PR Sentinel solve, and why are generic LLM chatbots insufficient?
**Answer**: Generic LLM chatbots suffer from three critical flaws when reviewing code:
1. **Hallucination of line numbers and code**: Chatbots frequently reference nonexistent files or invent line ranges. PR Sentinel enforces strict programmatic verification (`FindingValidator`), ensuring findings anchor exclusively to added/modified lines with verbatim evidence.
2. **Alert Fatigue & Duplication**: Generic prompts repeat known linter warnings. PR Sentinel runs deterministic static tools first (Ruff, Bandit), informs Gemini of existing detections to avoid redundant re-discovery, and merges overlapping findings into single deduplicated records.
3. **Security Vulnerabilities**: Chatbots can be jailbroken via prompt injection embedded in commit messages or code comments. PR Sentinel neutralizes delimiters (`</diff>` &rarr; `&lt;/diff>`) and treats all repository code as untrusted data within strict system envelopes.

### 2. Why was LangGraph selected over a linear script or Celery DAG?
**Answer**: LangGraph provides a stateful, cyclic directed graph abstraction with typed state transitions (`ReviewState`). Unlike a simple procedural script, LangGraph cleanly models:
- **Conditional Routing**: Every node checks `state["failed"]` and branches conditionally to `fail_review` while capturing structured error codes and stages.
- **State Serialization**: The entire review snapshot (hunks, context, findings, tokens, warnings) is managed in a single immutable-friendly dictionary, simplifying debugging and telemetry.
- **Extensibility**: Adding new stages (e.g. semantic dependency checks, multi-agent debates, or GitHub PR commenting) requires adding a node and edge without refactoring pipeline plumbing.

### 3. Why was an AST/symbol retriever chosen over vector embeddings (RAG)?
**Answer**: For pull request reviews, **AST keyword and symbol retrieval is strictly superior to vector search**:
- **Zero Latency & Drift**: Generative vector embeddings require generating embeddings for hundreds of files on every PR or maintaining an out-of-date vector index. AST parsing runs in milliseconds locally.
- **Deterministic Scope**: A compiler needs exact definitions of symbols referenced in the diff (e.g. `def process_user(...)`), not "semantically similar" documentation. The AST parser extracts exact symbol definitions from imported modules.
- **Zero External Infrastructure**: No vector database (Pinecone, Qdrant, Chroma) is required, keeping PR Sentinel self-contained and lightweight.

### 4. How does PR Sentinel prevent Server-Side Request Forgery (SSRF)?
**Answer**:
1. **Strict URL Grammar**: `app/services/url_parser.py` parses submitted URLs using Python's `urllib.parse` and regex. It permits only scheme `https://`, host `github.com`, and path pattern `/{owner}/{repo}/pull/{number}`.
2. **Rejection of Lookalikes & Bypass Tricks**: Explicit ports (`github.com:8080`), embedded credentials (`user:pass@github.com`), IP addresses, hex/octal encodings, punycode lookalikes, and query/fragment payloads are immediately rejected with HTTP 422.
3. **Outbound Destination Pinning**: In `GitHubClient`, all outbound HTTP requests are pinned to `https://api.github.com/`. Any attempt to redirect or divert traffic outside `api.github.com` triggers an immediate security abort.
4. **Disabled Redirects**: `follow_redirects=False` is enforced on HTTPX to prevent 301/302 redirects to cloud metadata services (`169.254.169.254`).

### 5. How are static analyzers executed safely against untrusted repository code?
**Answer**:
1. **Isolated CLI Flags**:
   - Ruff is executed with `--isolated`, ensuring it ignores repository `pyproject.toml` or `ruff.toml` files that might configure malicious extensions.
   - ESLint is executed with `--no-config-lookup -c <internal_path>`, forcing it to use PR Sentinel's audited internal configuration (`backend/tools/eslint/eslint.config.mjs`) rather than untrusted repository configs (`eslint.config.js`).
2. **Exclusion of Config Files**: Repositories containing arbitrary configuration scripts (`setup.py`, `package.json`, `eslint.config.js`) have those files filtered out before being written to the temporary analysis directory.
3. **Subprocess Isolation**: Analyzers are spawned via `asyncio.create_subprocess_exec` with explicit parameter lists. `shell=True` is strictly forbidden. Subprocess environments are stripped of all API keys via `get_safe_environment()`, and execution is bound to a 30-second timeout.

### 6. How does PR Sentinel defeat prompt injection in diffs and commit messages?
**Answer**:
1. **XML Data Envelopes**: All untrusted data (diff patches, context snippets, PR titles, author names) is enclosed in tags (`<pr_data>`, `<files>`, `<diff>`, `<context>`).
2. **Delimiter Neutralization**: In `app/ai/prompts.py`, `neutralize_delimiters()` scans all untrusted text and replaces closing XML tags (e.g. `</diff>`, `</file>`, `</pr_data>`) with HTML entities (`&lt;/diff>`). This prevents attackers from closing the data block and issuing fake system commands.
3. **Attribute Escaping**: Metadata attributes like PR titles or author handles are attribute-escaped (`"`, `'`, `&`, `<`, `>`).
4. **System Instructions & Zero Tools**: The system prompt establishes that everything inside `<pr_data>` is passive data to review, never instructions to execute. Gemini is configured with **zero tools/functions**, ensuring it cannot invoke APIs or trigger external actions even if persuaded.

### 7. How does the system verify that AI findings are not hallucinated?
**Answer**: In `app/services/validation.py`, `FindingValidator` tests every emitted `AIFinding`:
1. **File Existence**: The finding's `file_path` must match an actual file in the PR.
2. **Line Boundary Check**: The finding's line range `[line_start, line_end]` must intersect modified lines in the diff hunk.
3. **Verbatim Evidence Match**: The quoted code snippet in `finding.evidence` must match verbatim against the target file lines in the repository. If the model imagines code that does not exist, the finding is dropped.
4. Dropped findings are tracked in `dropped_findings_count` with explicit drop reason tags.

### 8. How does finding deduplication work?
**Answer**: In `app/services/dedupe.py`, every finding is assigned a deterministic SHA-256 fingerprint:
$$\text{Fingerprint} = \text{SHA256}(\text{path} + ":" + \text{line\_start} + ":" + \text{category} + ":" + \text{rule\_id})$$
- If Bandit flags an SQL injection on line 42 and Gemini flags the same issue on line 42 under the `security` category, their fingerprints match.
- The deduplicator merges them, retaining the highest severity and confidence score, while documenting both sources.

### 9. Why does the current background-task architecture have limitations?
**Answer**:
- PR Sentinel currently dispatches review jobs using FastAPI's in-process `BackgroundTasks`.
- **Limitation**: `BackgroundTasks` run within the Uvicorn worker process. If the server restarts or crashes mid-review, in-flight jobs are terminated.
- **Current Mitigation**: PR Sentinel implements an automated recovery check in `lifespan` that detects reviews left in `running` status upon server startup and marks them as `failed` with error code `server_restart`. The frontend polling hook handles backend drops gracefully and recovers when the server restarts.
- **Production Solution**: In production, `BackgroundTasks` should be replaced with an external message queue like Celery or ARQ backed by Redis or RabbitMQ, with idempotent worker tasks and exponential backoff retry.

### 10. How is SQLite configured for concurrency and persistence?
**Answer**:
- SQLite is configured with Write-Ahead Logging (`PRAGMA journal_mode=WAL`) and foreign key constraints (`PRAGMA foreign_keys=ON`).
- WAL mode allows concurrent readers to read without blocking writers, and writers do not block readers.
- `connect_args={"check_same_thread": False}` is used with SQLAlchemy's connection pool to allow async worker threads to access sessions safely.
- In Docker, the database resides in `/app/data/pr_sentinel.db`, mounted to the Docker named volume `pr_sentinel_sqlite_data` to ensure persistence across container recreation.

---

## 3. Top Three Realistic Future Improvements

1. **Distributed Queue Architecture (Celery/Redis)**: Replace FastAPI `BackgroundTasks` with Celery or Temporal. This enables distributed worker pools, persistent task queues across deployments, and reliable task retries.
2. **GitHub App Webhook Integration & Inline PR Comments**: Transition from manual PR URL submission to an automated GitHub App that listens to `pull_request.opened` and `pull_request.synchronize` webhooks (`X-Hub-Signature-256`), and posts verified findings directly as inline review comments on GitHub.
3. **Hardened MicroVM / OCI Container Sandboxing for Analyzers**: While static linters currently run as isolated OS subprocesses with sanitized environments, running linters inside ephemeral, kernel-isolated microVMs (e.g. Firecracker, gVisor) would provide complete defense against zero-day Linux sandbox breakouts in multi-tenant environments.
