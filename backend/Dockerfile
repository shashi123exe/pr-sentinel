# ==============================================================================
# PR Sentinel — Production Backend Dockerfile (Multi-stage)
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Dependency Builder
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install basic build tools required for wheel compilation if any
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc python3-dev && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment and install pinned runtime dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Minimal Runtime Image
# ------------------------------------------------------------------------------
FROM python:3.12-slim AS runner

WORKDIR /app/backend

# Runtime environment settings
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app/backend" \
    DATABASE_URL="sqlite:////app/data/pr_sentinel.db"

# Create unprivileged application user and persistent storage directory
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser && \
    mkdir -p /app/data /app/backend && \
    chown -R appuser:appuser /app

# Copy isolated virtualenv from builder stage
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

# Copy backend application source code only (no tests, .env, or dev tooling)
COPY --chown=appuser:appuser app /app/backend/app

# Switch to non-root user
USER appuser

# Document backend listening port
EXPOSE 8000

# Health check using existing /api/health endpoint via Python standard library
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).getcode() == 200 else 1)"

# Start production server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
