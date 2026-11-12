# PR Sentinel — Deployment & Infrastructure Guide

This guide details the deployment procedures, hosting topology, persistence architecture, cost controls, and security configurations for PR Sentinel.

---

## 1. Architecture & Hosting Topology

PR Sentinel is deployed on **Render** as a unified single-service Docker container serving both the compiled React Single Page Application (SPA) and the FastAPI backend API on the same domain and port, or on self-hosted environments via **Docker Compose**.

```mermaid
flowchart TD
    subgraph Client["Client Environment"]
        Browser["User Browser / Client"]
        SessionStore[("sessionStorage\n(pr_sentinel_access_token)")]
        Browser <-->|Bearer Token Injected| SessionStore
    end

    subgraph Render["Render Cloud (Single Web Service)"]
        EdgeProxy["Render Edge Proxy\n(SSL Termination & Client IP Forwarding)"]

        subgraph Container["pr-sentinel Unified Docker Container"]
            Uvicorn["Uvicorn ASGI Server\n(--workers 1 --proxy-headers --forwarded-allow-ips='*')"]
            FastAPI["FastAPI Application (Python 3.12)"]
            StaticServe["Static File Serving (SERVE_FRONTEND=true)\nSPA Fallback: /index.html\n(Never Shadows /api/*, /docs, /redoc)"]
            ReactBundle["Compiled React 19 SPA (/app/frontend/dist)\n(Built with Node.js 22 & npm ci)"]
            ProcessLock["_review_limit_lock (threading.Lock)\nAtomic Rolling 24h Quota Enforcement"]

            Uvicorn --> FastAPI
            FastAPI -->|Route: /api/*| Endpoints["REST API Endpoints"]
            FastAPI -->|Route: deep links & assets| StaticServe
            StaticServe --> ReactBundle
            Endpoints --> ProcessLock
        end

        Postgres[("Optional Render PostgreSQL\n(Paid Production Tier Recommended)\nDriver: psycopg 3.2.4\n30-Day Expiry on Free Tier")]
        SQLite[("Local Ephemeral SQLite\n(/app/pr_sentinel.db)\nLost on restart / spin-down")]
    end

    subgraph External["External Cloud Integrations"]
        GitHub["GitHub REST API\n(PR Metadata, Unified Diffs)"]
        Gemini["Google Gemini API\n(2.5/3.5 Pro & Flash Analysis)"]
    end

    Browser -->|HTTPS /api/* & Static Assets| EdgeProxy
    EdgeProxy -->|Forwarded Headers (X-Forwarded-For)| Uvicorn
    Endpoints -->|Health Probe / State| SQLite
    Endpoints -.->|Optional Managed Connection| Postgres
    Endpoints -->|HTTPS Fetch Diff / PR| GitHub
    Endpoints -->|HTTPS Structured Prompts| Gemini
```

### Advantages of Single-Service Docker Architecture:
1. **Zero CORS Friction**: Frontend assets and REST API endpoints are served from the exact same origin and port, eliminating cross-origin resource sharing failures and preflight latency.
2. **Deterministic Routing**: FastAPI serves `/api/*` with dedicated JSON error envelopes, documents endpoints at `/docs` and `/redoc`, and routes all other deep client links to `/index.html` via the SPA fallback handler.
3. **Optimized Build**: Multi-stage `Dockerfile.render` uses Node.js 22 to compile the Vite/React bundle, installs Python 3.12 dependencies into an isolated virtualenv, and produces an unprivileged (`appuser`), minimal production image.

---

## 2. Render Blueprint Specification (`render.yaml`)

PR Sentinel includes a root [`render.yaml`](../render.yaml) defining the unified Web Service as an Infrastructure-as-Code (IaC) Blueprint.

### Unified Service Configuration:
- **Type**: `web`
- **Name**: `pr-sentinel`
- **Runtime**: `docker`
- **Dockerfile Path**: `./Dockerfile.render`
- **Instance Count**: `numInstances: 1` (strictly set to 1 to guarantee concurrency and in-memory rate-limiting safety)
- **Plan**: `free`
- **Health Check Path**: `/api/health`
- **Environment Variables**:
  - `SERVE_FRONTEND`: `"true"` (enables static SPA serving and client fallback routing)
  - `DATABASE_URL`: `sqlite:///./pr_sentinel.db` (or connection string to paid PostgreSQL)
  - `MAX_REVIEWS_PER_DAY`: `"20"` (rolling 24-hour review ceiling protecting Gemini LLM budget)
  - `LOG_LEVEL`: `INFO` (structured logging level)
  - `GEMINI_API_KEY`: Google Gemini API key (`sync: false`)
  - `APP_ACCESS_TOKEN`: Shared bearer token gate (`sync: false`)
  - `GITHUB_TOKEN`: GitHub personal access token for higher API limits (`sync: false`)
  - `GEMINI_MODEL`: Optional model identifier override (`sync: false`)

> [!NOTE]
> **Blueprint Schema Validation Status**: Local YAML syntax is verified valid using PyYAML. Exact Render Blueprint schema validation against Render's cloud API remains unverified locally due to the absence of the Render CLI binary on the host environment.

---

## 3. Database Persistence Realities & Recommendations

Choosing the right database backend depends on deployment constraints and data durability requirements:

### Free Tier SQLite Storage Ephemerality
- **Free Web Service File System**: On Render's Free tier, disk storage is **ephemeral**.
- **Data Loss Scenarios**: The SQLite database file (`pr_sentinel.db`) is **completely erased and reset** when:
  1. The backend service redeploys (code update or config change).
  2. The service restarts manually or due to an error.
  3. The free instance spins down due to 15 minutes of inactivity.
- **Use Case**: Appropriate only for transient demos or exploratory evaluations.

### Free Tier Render PostgreSQL Constraints
Render offers a Free PostgreSQL database tier, but with severe operational restrictions:
1. **30-Day Expiry & Destruction**: Free PostgreSQL databases automatically **expire and are destroyed after 30 days**. All stored reviews, triage states, and configuration are permanently lost.
2. **1 GB Storage Limit**: Hard cap on database size with strict connection throttles.
3. **No Automated Backups**: Free databases do not include automated daily backups or point-in-time recovery (PITR).

### Production Recommendation
For long-term retention and reliable production operations:
- Use a **Paid Render PostgreSQL** instance ($7/month Starter plan) or an external managed provider (Neon, Supabase, AWS RDS).
- Paid instances include automated daily backups, persistent SSD storage, and no expiration window.

### PostgreSQL Driver & URL Normalization
- **Psycopg 3 Driver**: The backend pins `psycopg[binary]==3.3.8` in `requirements.txt`.
- **URL Normalization**: Both legacy `postgres://` (Render default) and standard `postgresql://` connection strings are normalized automatically to `postgresql+psycopg://` in `normalize_database_url`.
- **Connection Pool Tuning**: `build_engine` configures PostgreSQL connections with:
  - `pool_pre_ping=True` (detects and recycles disconnected sockets)
  - `pool_size=5` (maintains up to 5 pooled connections)
  - `max_overflow=10` (bursts up to 10 additional connections)
  - `pool_timeout=30.0` (blocks for up to 30 seconds before timing out)
- **SQLite Isolation**: PRAGMA foreign keys and WAL execution hooks are applied strictly to SQLite engines and are never attached to PostgreSQL engines.

### Test Database Safety Safeguards
Automated testing contains strict safeguards via `validate_test_database_url` using `sqlalchemy.engine.make_url`:
- **Typed Component Parsing**: Decomposes URLs into `host`, `database`, `username`, and `password` components.
- **Credential Substring Bypass Prevention**: Validates `database` independently from `username` and `password`. Credentials containing the word `"test"` (e.g. `postgresql://test_user:pass@ep-prod.render.com/prod_db`) are rejected.
- **Cloud Infrastructure Rejection**: Unconditionally rejects URLs pointing to production cloud hosts including `render.com`, `rds.amazonaws.com`, `azure.com`, `neon.tech`, and `supabase.co`.
- **Safe Test DB Marker**: Requires test database names to explicitly include `test` or `disposable`.
- **Test Marker & Skipping**: Live PostgreSQL integration tests are tagged with `@pytest.mark.postgres` and skip cleanly when `TEST_DATABASE_URL` is unset.

---

## 4. Concurrency-Safe Daily Review Limits (`MAX_REVIEWS_PER_DAY`)

To protect against runaway Gemini LLM costs and quota exhaustion, PR Sentinel enforces an atomic rolling 24-hour limit on review submissions (`POST /api/reviews`).

### Technical Implementation:
- **Rolling 24-Hour Window**: Calculates cutoff as `datetime.now(UTC) - timedelta(hours=24)`.
- **Inclusive Quota Accounting (Including Failed Reviews)**: Counts **all** reviews created at or after the cutoff timestamp, **including `ReviewStatus.FAILED`**.
  > [!IMPORTANT]
  > **Spending Protection Rationale**: Failed reviews consume upstream LLM tokens and GitHub API rate limits during early pipeline execution. If failed reviews were exempt, an adversary or misconfigured client could repeatedly trigger failed review runs and exhaust the operator's Google Cloud budget.
- **Error Code**: When the quota is reached, subsequent submissions receive HTTP 429 with error code `daily_limit_reached`.
- **Process Mutex Lock**: A process-level mutex (`_review_limit_lock = threading.Lock()`) serializes concurrent requests through the count query, limit evaluation, and initial review record insertion within a single atomic critical section.
- **Race Condition Elimination**: If two concurrent requests arrive simultaneously when only 1 review slot remains:
  - Request 1 enters the critical section, sees remaining capacity, commits the review, and releases the lock.
  - Request 2 enters the critical section, detects that capacity is exhausted, and immediately receives HTTP 429 (`daily_limit_reached`).
  - Overbooking is mathematically impossible under serialized lock execution.
- **Restart Persistence**: Because review records and their timestamps are committed directly to the database, the rolling count survives server restarts when the underlying database storage persists.
- **Disabling the Limit**: Set `MAX_REVIEWS_PER_DAY=0` to disable the daily quota.

> [!CAUTION]
> **Free-Tier Ephemeral Storage Limitation on Daily Quota**:
> On Render's Free tier, the filesystem is ephemeral. If the instance spins down due to 15 minutes of inactivity, restarts, or is redeployed, the local SQLite database is erased. **Consequently, the SQLite-based daily review limit will reset to zero upon filesystem reset.**
> **Do not treat the free SQLite daily limit as a guaranteed or durable spending ceiling in free mode.** For reliable, durable daily quota enforcement across restarts and deployments, a **paid persistent disk** (`/app/data`) or a **persistent PostgreSQL database** is strictly required. Operators must also configure Google Cloud Gemini hard quotas in Google Cloud Console as an independent, secondary layer of spending protection.

> [!WARNING]
> **Single-Worker Requirement**:
> Concurrency safety relies on running Uvicorn with `--workers 1` and Render with `numInstances: 1`. In a multi-worker or horizontally scaled deployment, in-process mutex locks do not synchronize across processes. For multi-worker deployments, a distributed locking mechanism (e.g., Redis Redlock or PostgreSQL advisory locks via `pg_advisory_xact_lock`) must be implemented.

---

## 5. Reverse Proxy Headers & Rate Limiting

The unified container runs Uvicorn configured with `--proxy-headers --forwarded-allow-ips="*"`.

### Client IP Resolution & Rate Limiting:
- **Render Edge Proxy**: Render terminates TLS and forwards incoming requests to the container, appending the connecting client IP to the `X-Forwarded-For` header.
- **Proxy Headers Processing**: Uvicorn's `ProxyHeadersMiddleware` inspects `X-Forwarded-For` and sets `request.client.host` to the real client IP.
- **SlowAPI Integration**: The rate limiter keys on `get_remote_address`, ensuring that rate limits (e.g. 5 requests/minute on review creation) are partitioned per client IP rather than aggregating all users under Render's proxy IP.
- **Security Tradeoff**:
  - Setting `--forwarded-allow-ips="*"` trusts forwarded headers. This is safe and recommended behind Render's edge proxy because Render sanitizes inbound headers and appends the verified IP.
  - If deploying directly to the public internet without a reverse proxy, `--forwarded-allow-ips` should be restricted to known CIDR ranges to prevent spoofed `X-Forwarded-For` headers from bypassing IP-based rate limiting.

---

## 6. Application Shared Access Token Gate (`APP_ACCESS_TOKEN`)

PR Sentinel provides an optional shared bearer access token gate to restrict API access without the complexity of a full multi-user authentication system.

### How It Works:
- **Configuration**: Set `APP_ACCESS_TOKEN` in backend environment variables.
- **Optional/Disabled by Default**: When unset or empty, access gating is disabled, preserving frictionless local development and automated testing.
- **Header Verification**: When configured, all non-health API endpoints require either the standard `X-Access-Token` custom header or an `Authorization: Bearer` header:
  ```http
  X-Access-Token: <your-shared-access-token>
  ```
  *(Or `Authorization: Bearer <your-shared-access-token>` for backward compatibility).*
- **Constant-Time Comparison**: Validates tokens using `secrets.compare_digest` to eliminate timing-attack vulnerabilities.
- **Public Endpoints**: `/api/health`, `/docs`, `/redoc`, and `/openapi.json` remain unauthenticated so health monitors, uptime probes, and API schemas function uninterrupted.

### Frontend Token Storage & Modal Dialog:
- **Session-Only Storage**: The frontend stores the token exclusively in browser `sessionStorage` (`pr_sentinel_access_token`). It is stored per-tab and erased when the browser tab closes.
- **Never Persistent**: Never stored in `localStorage`, never placed in URL query parameters, and never logged to console.
- **Automatic 401 Re-Authentication & Form Draft Preservation**:
  - If an API request returns HTTP 401 (`unauthorized`), the API client purges the stored token and dispatches a `pr-sentinel:unauthorized` window event.
  - The globally mounted `<AccessTokenModal />` dialog automatically appears, prompting the user to enter the access token.
  - Upon submission, the new token is saved to `sessionStorage` and dispatches `pr-sentinel:token-updated`.
  - Form drafts (e.g. pending PR URL input) are preserved intact without a destructive page reload, enabling seamless immediate retry.

---

## 7. Gemini Cost Protection & Key Rotation

Using LLM APIs in production requires a multi-layered cost defense strategy:

### Google Cloud Billing Budget Alerts vs. Hard Quotas
- **Budget Alerts**: Google Cloud Billing allows setting budget thresholds (e.g., \$50/month). However, budget alerts **only send email notifications**; they **do not automatically shut off API access** or prevent further spend.
- **Hard Quotas (API Rate Limits)**: Google Cloud Console allows setting hard quotas (maximum requests per minute and per day) on the Gemini API service. When reached, Gemini rejects further requests with HTTP 429.
- **Application Ceiling**: `MAX_REVIEWS_PER_DAY` provides an immediate, in-application hard ceiling. It prevents excessive Gemini token consumption before API calls are ever dispatched.

### Emergency Gemini Key Rotation Procedure
If a Gemini API key is compromised:
1. **Generate New Key**: In Google Cloud Console, navigate to **APIs & Services > Credentials** and generate a new Gemini API Key.
2. **Update Render Environment Variable**: In the Render Dashboard under **Environment**, update `GEMINI_API_KEY` with the new value.
3. **Trigger Manual Deploy**: Click **Manual Deploy > Deploy latest commit** to restart the container with the updated key.
4. **Verify Functionality**: Submit a test review to confirm successful analysis.
5. **Revoke Old Key**: In Google Cloud Console, delete or disable the previous compromised API key.

---

## 8. Step-by-Step Render Deployment Walkthrough

### Step 1: Connect GitHub Repository
1. Push your repository to GitHub.
2. In the Render Dashboard, select **New > Blueprint**.
3. Connect your GitHub repository containing `render.yaml`.

### Step 2: Configure Environment Variables
Render will detect the unified `pr-sentinel` service and prompt for un-synced secrets:
- `GEMINI_API_KEY`: Your Google Gemini API key.
- `APP_ACCESS_TOKEN`: (Optional) Secret passphrase for API access control.
- `GITHUB_TOKEN`: (Optional) GitHub personal access token for higher rate limits.
- `DATABASE_URL`: (Optional) Connection string to paid Render PostgreSQL or external DB.

### Step 3: Deploy and Verify
1. Click **Apply**. Render will trigger the multi-stage Docker build from `Dockerfile.render`.
2. Verify container startup: Check logs for `PR Sentinel backend startup complete.`
3. Verify health probe: Access `https://<service-name>.onrender.com/api/health` — expect `{"status": "ok", "database": "ok"}`.
4. Verify web application: Open `https://<service-name>.onrender.com` in your browser. Both the React SPA and API are live on the same URL.
5. If `APP_ACCESS_TOKEN` is configured, enter the token in the modal prompt or under **Settings**.

---

## 9. Docker Compose Deployment (Phase 11 Preservation)

The Phase 11 multi-container Docker Compose setup remains fully supported and operational for local container testing:

```bash
# 1. Configure environment in backend/.env or root .env
cp .env.example backend/.env

# 2. Build and launch services
docker compose up --build -d

# 3. Access web application
# Frontend: http://localhost:8080
# Backend API: http://localhost:8000/api/health
```
