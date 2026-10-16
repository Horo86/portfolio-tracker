"""Business endpoints under /api/v1 (ADR-0004)."""

from fastapi import APIRouter

from portfolio_tracker.api.routes import accounts, admin, assets, auth, me, reports, transactions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(admin.router)
api_router.include_router(accounts.router)
api_router.include_router(assets.router)
api_router.include_router(transactions.router)
api_router.include_router(reports.router)
