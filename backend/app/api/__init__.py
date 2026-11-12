"""API routing module for PR Sentinel."""

from fastapi import APIRouter, Depends

from app.api.auth import verify_access_token
from app.api.findings import router as findings_router
from app.api.health import router as health_router
from app.api.reviews import router as reviews_router
from app.api.settings import router as settings_router

api_router = APIRouter(prefix="/api")
# Health check endpoint remains public for orchestration and uptime monitoring
api_router.include_router(health_router)

# Core API endpoints protected by optional shared bearer access token
api_router.include_router(settings_router, dependencies=[Depends(verify_access_token)])
api_router.include_router(reviews_router, dependencies=[Depends(verify_access_token)])
api_router.include_router(findings_router, dependencies=[Depends(verify_access_token)])

__all__ = ["api_router"]
