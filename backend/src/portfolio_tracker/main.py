"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from starlette.exceptions import HTTPException
from starlette.types import Scope

from portfolio_tracker import __version__
from portfolio_tracker.api.errors import install_error_handlers
from portfolio_tracker.api.limits import BodyLimit, BodyTooLarge, body_too_large, bound_for
from portfolio_tracker.api.routes import api_router
from portfolio_tracker.auth.passwords import Passwords
from portfolio_tracker.auth.sessions import SessionPolicy
from portfolio_tracker.auth.throttle import LoginThrottle
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.logging_setup import setup_logging
from portfolio_tracker.migrations_runner import run_migrations

log = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    version: str
    database: Literal["ok", "error"]


class SpaStaticFiles(StaticFiles):
    """Serves the built frontend; unknown non-API paths fall back to index.html."""

    async def get_response(self, path: str, scope: Scope):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


def create_app(
    settings: Settings | None = None,
    *,
    passwords: Passwords | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Application factory. `passwords` and `clock` are injectable for tests only (cheap
    hashing, controlled time); they are not configuration."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level)
        engine = create_db_engine(settings.db_path)
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
        app.state.engine = engine
        app.state.passwords = passwords or Passwords()
        log.info("application started", extra={"version": __version__})
        if settings.cookie_secure:
            log.info(
                "session cookies are Secure: use https (reverse proxy) or localhost; over plain "
                "http from another host browsers will not keep the session "
                "(PT_COOKIE_SECURE=false only on a trusted LAN)"
            )
        yield
        engine.dispose()

    app = FastAPI(
        title="Portfolio Tracker",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    app.add_exception_handler(BodyTooLarge, body_too_large)
    app.add_middleware(BodyLimit, bound_for=bound_for)  # before any body is read
    app.state.settings = settings
    app.state.clock = clock or (lambda: datetime.now(UTC))
    app.state.login_throttle = LoginThrottle()
    app.state.cookie_warning_logged = False
    app.state.session_policy = SessionPolicy(
        idle=timedelta(days=settings.session_idle_days),
        absolute=timedelta(days=settings.session_absolute_days),
    )

    @app.get("/api/health", response_model=HealthResponse, tags=["operations"])
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            log.exception("health check: database unavailable")
            body = HealthResponse(status="error", version=__version__, database="error")
            return JSONResponse(status_code=503, content=body.model_dump())
        return HealthResponse(status="ok", version=__version__, database="ok")

    app.include_router(api_router)

    if settings.static_dir and (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="web")

    return app
