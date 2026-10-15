"""Business endpoints under /api/v1 (ADR-0004)."""

from fastapi import APIRouter

from portfolio_tracker.api.routes import auth, me

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(me.router)
