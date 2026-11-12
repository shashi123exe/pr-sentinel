# Contributing to PR Sentinel

Thank you for your interest in contributing to **PR Sentinel**! We welcome contributions to enhance security analysis, improve developer experience, and expand static analysis capabilities.

---

## 1. Prerequisites

Before setting up the project, ensure you have the following installed on your workstation:
- **Python**: Version 3.12 or higher.
- **Node.js**: Version 22.x or higher, with `npm` (v10+).
- **Git**: Version 2.40 or higher.
- **Docker** *(Optional)*: Docker Engine 24+ and Docker Compose 2.24.0+ (required if testing multi-container deployment).

---

## 2. Local Development Setup

### Clone the Repository
```bash
git clone https://github.com/shashi123exe/pr-sentinel.git
cd pr-sentinel
```

### Backend Setup
1. Create and activate a Python 3.12 virtual environment:
   ```powershell
   # Windows PowerShell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   ```bash
   # Linux / macOS
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   ```
2. Install pinned runtime and development dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements-dev.txt
   ```
3. Configure the backend environment file:
   ```bash
   cp ../.env.example .env
   ```
   Edit `.env` to configure your `GEMINI_API_KEY` (and optional `GITHUB_TOKEN`).

### Frontend Setup
1. Install dependencies using clean install:
   ```bash
   cd ../frontend
   npm install
   ```

### Running Development Servers
- **Option A (PowerShell Automated Launcher)**:
  ```powershell
  # From workspace root
  .\scripts\dev.ps1 -LaunchWindows
  ```
- **Option B (Manual Terminals)**:
  - Terminal 1 (Backend): `cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`
  - Terminal 2 (Frontend): `cd frontend && npm run dev -- --host 127.0.0.1 --port 5173`

---

## 3. Testing & Verification

PR Sentinel enforces a strict, regression-tested quality bar. All 476 automated tests must pass before opening a pull request.

### Running Backend Tests (420 Tests Passed, 1 Skipped)
```bash
# From workspace root
backend/.venv/Scripts/pytest.exe -q backend/tests

# Run with test coverage
backend/.venv/Scripts/pytest.exe -q --cov=app --cov-report=term-missing backend/tests
```

### Running Frontend Tests (56 Tests across 15 files)
```bash
cd frontend
npm test -- --run
```

### Automated Security & Linter Suite
```powershell
# Runs ruff, bandit, oxlint, vitest, and security tests
.\scripts\security-check.ps1
```

---

## 4. Linting and Code Formatting

We use automated linters to maintain uniform code quality:

### Python (Backend)
- **Linter**: `ruff check backend`
- **Formatter**: `ruff format backend`
- **Security Scanner**: `bandit -r backend/app -q`

### TypeScript & React (Frontend)
- **Linter**: `npm --prefix frontend run lint` (`oxlint src`)
- **Type Checking & Build**: `npm --prefix frontend run build` (`tsc -b && vite build`)
- **Dependency Audit**: `npm --prefix frontend audit --omit=dev`

---

## 5. Branch and Pull Request Conventions

1. **Branch Naming**:
   - `feature/<short-description>`: New capabilities or enhancements.
   - `fix/<bug-description>`: Bug fixes and contract alignment.
   - `security/<hardening-topic>`: Security validations, rate limiting, and sandbox hardening.
   - `docs/<topic>`: Documentation, guides, and demo assets.
2. **Commit Hygiene**:
   - Write clear, imperative commit messages: `feat: add Bandit severity mapping to category taxonomy`.
   - Never commit `.env`, API keys, tokens, or personal credentials.
   - Run `git diff --check` before committing to avoid whitespace and line-ending anomalies.
3. **Submitting a Pull Request**:
   - Describe what changed and why.
   - Include test output demonstrating 100% pass rates.
   - Confirm that no new external network calls are introduced in automated test suites (all unit/integration tests must use mocked clients).

---

## 6. Licensing & Intellectual Property

By contributing to **PR Sentinel**, you agree that your contributions will be licensed under the project's [MIT License](LICENSE). Third-party dependencies, libraries, and assets retain their respective upstream open-source licenses.
