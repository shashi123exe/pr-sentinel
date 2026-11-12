"""FastAPI application factory, lifespan management, and middleware setup."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api import api_router
from app.config import WORKSPACE_DIR, Settings, get_settings
from app.db.models import Review, ReviewStatus
from app.db.session import SessionLocal, engine, init_db
from app.errors import AppError, register_error_handlers
from app.graph.builder import get_compiled_graph
from app.logging_config import RequestIDMiddleware, setup_logging
from app.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle tasks."""
    target_engine = (
        getattr(app.state, "engine", engine)
        if app and hasattr(app, "state")
        else engine
    )
    session_factory = (
        getattr(app.state, "session_factory", SessionLocal)
        if app and hasattr(app, "state")
        else SessionLocal
    )

    # 1. Initialize database tables and seed default AppSettings row
    init_db(engine_override=target_engine)

    # 2. Recover from previous ungraceful shutdown: mark running reviews as failed
    with session_factory() as db:
        running_reviews = db.scalars(
            select(Review).where(Review.status == ReviewStatus.RUNNING)
        ).all()
        for review in running_reviews:
            review.status = ReviewStatus.FAILED
            review.error_code = "server_restart"
            review.error_message = (
                "Review execution was interrupted by an unexpected server restart."
            )
        if running_reviews:
            db.commit()
            logger.warning(
                "Marked %d stale 'running' reviews as failed due to server restart.",
                len(running_reviews),
            )

    # 3. Compile the LangGraph review state machine once during startup
    app.state.compiled_graph = get_compiled_graph()

    logger.info("PR Sentinel backend startup complete.")
    yield
    logger.info("PR Sentinel backend shutting down.")


def create_app(settings_override: Settings | None = None) -> FastAPI:
    """FastAPI application factory with configuration, middleware, and routers."""
    app_settings = settings_override or get_settings()

    # Configure structured logging and secret redaction
    setup_logging(app_settings.LOG_LEVEL)

    application = FastAPI(
        title="PR Sentinel API",
        description="AI-Powered GitHub Pull Request Code Review Platform API",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Attach SlowAPI state
    application.state.limiter = limiter

    # Request ID middleware
    application.add_middleware(RequestIDMiddleware)

    # CORS configuration
    origins = app_settings.cors_origins_list
    allow_credentials = False if "*" in origins else True

    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins if origins else ["http://localhost:5173"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Error handlers
    register_error_handlers(application)

    # Register API routes under /api
    application.include_router(api_router)

    # Optional compiled React SPA static file serving (Single-service deployment mode)
    if app_settings.SERVE_FRONTEND:
        raw_dist = (
            Path(app_settings.FRONTEND_DIST_DIR)
            if app_settings.FRONTEND_DIST_DIR
            else WORKSPACE_DIR / "frontend" / "dist"
        )
        resolved_dist = raw_dist.resolve()

        @application.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(request: Request, full_path: str) -> FileResponse:
            """Serve production frontend assets with SPA fallback for client routing."""
            # Ensure unknown /api requests return standard JSON 404 envelope, never HTML
            if full_path.startswith("api/") or full_path == "api":
                raise AppError(
                    code="not_found",
                    message=f"API endpoint '/{full_path}' not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

            # Resolve requested target and verify path boundary containment
            target = (resolved_dist / full_path).resolve()
            try:
                is_contained = target.is_relative_to(resolved_dist)
            except (ValueError, AttributeError):
                is_contained = False

            if not is_contained:
                raise AppError(
                    code="path_traversal",
                    message=(
                        "Requested path traverses outside the frontend distribution."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            # Serve static assets only when verified strictly inside dist directory
            if target.is_file():
                return FileResponse(target)

            # SPA fallback for valid frontend client routes
            index_html = (resolved_dist / "index.html").resolve()
            try:
                index_contained = index_html.is_relative_to(resolved_dist)
            except (ValueError, AttributeError):
                index_contained = False

            if index_contained and index_html.is_file():
                return FileResponse(index_html)

            raise AppError(
                code="frontend_not_built",
                message="Frontend production bundle not found.",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    return application


# Global application instance for uvicorn
app = create_app()
