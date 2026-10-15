"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level)
        engine = create_db_engine(settings.db_path)
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
        app.state.engine = engine
        log.info("application started", extra={"version": __version__})
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

    if settings.static_dir and (settings.static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=settings.static_dir, html=True), name="web")

    return app
