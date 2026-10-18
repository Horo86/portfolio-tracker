"""Market data for the Administrator (D-121, D-135): start a run now, and the status the M6
screens read. Administrator only: 404 for everyone else (route matrix). Responses carry counts
and error codes only, never an ISIN, an instrument, a listing or a symbol, which would show
what users hold (D-116).

The last run's counts are shown only from `SHOWN_COUNTS` (final review M-2, D-135): a count
that changes when an instrument is new (created, classified, searched, back-filled) would tell
an Administrator who adds an asset and runs now whether another user already holds that
security. They stay in the server's job log. The per-engine totals remain: an accepted
residual (D-135).
"""

from datetime import datetime, time
from typing import Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from portfolio_tracker.api.deps import AdminAuth, DbSession
from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.repositories import audit, instruments, job_runs, market_config
from portfolio_tracker.services.scheduler import Trigger

router = APIRouter(prefix="/admin/market-data", tags=["administration"])

# Counts that do not depend on whether an instrument was new in the run.
SHOWN_COUNTS = frozenset(
    {
        "assets_linked",  # includes the Administrator's own asset either way
        "targets", "switched", "level_refused", "switch_waiting",  # listed instruments only
        "reclassified", "confirmed", "deactivated",  # the re-check of inferred classes
        "recheck_pending", "recheck_abandoned",
        "fx_currencies_updated", "fx_rates_stored",  # per currency (D-122 residual)
    }
)  # fmt: skip


class RunOut(BaseModel):
    # QUEUED, or ALREADY_QUEUED: a job waiting to start covers it (every job does a full run)
    queued: Literal["QUEUED", "ALREADY_QUEUED"]


class LastRunOut(BaseModel):
    kind: str
    result: str  # OK | PARTIAL | FAILED | INTERRUPTED
    started_at: datetime
    finished_at: datetime | None
    counts: dict[str, int]  # only `SHOWN_COUNTS`; the job log keeps every count
    errors: dict[str, int]  # by error code


class EngineChecksOut(BaseModel):
    name: str
    checks: dict[str, int]  # instruments by the status of that engine's last search


class StatusOut(BaseModel):
    market_data: Literal["ON", "OFF"]
    daily_time_utc: time
    running: str | None  # the kind of the job running now
    next_run_at: datetime | None  # null while market data is off
    recheck_pending: bool  # a listing re-check waits for the next run (D-132)
    last_run: LastRunOut | None
    engines: list[EngineChecksOut]


@router.post(
    "/run",
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"description": "MARKET_DATA_RUNNING or MARKET_DATA_OFF"}},
)
def run_now(auth: AdminAuth, request: Request, db: DbSession) -> RunOut:
    """Queue a full market data run now (D-121); refused while one runs."""
    market_data = request.app.state.market_data
    if not market_data.on:
        raise ApiError(409, "MARKET_DATA_OFF", "market data is off on this instance")
    queued = market_data.scheduler.trigger("MANUAL")
    if queued is Trigger.BUSY:
        raise ApiError(409, "MARKET_DATA_RUNNING", "a market data run is in progress")
    audit.record(
        db, "market_data.run_requested", actor_id=auth.user.id, after={"queued": queued.value},
        ip=client_ip(request),
    )  # fmt: skip
    return RunOut(queued=queued.value)


@router.get("/status")
def market_status(auth: AdminAuth, request: Request, db: DbSession) -> StatusOut:
    """Counts only (D-135): the last run's counts and error codes, and per engine how many
    instruments its last search left in each status."""
    market_data = request.app.state.market_data
    scheduler = market_data.scheduler
    last = job_runs.last_run(db)
    engines: dict[str, dict[str, int]] = {}
    for (provider, check_status), count in sorted(instruments.check_counts(db).items()):
        engines.setdefault(provider, {})[check_status] = count
    return StatusOut(
        market_data="ON" if market_data.on else "OFF",
        daily_time_utc=request.app.state.settings.daily_at,
        running=scheduler.running,
        next_run_at=scheduler.next_daily if market_data.on else None,
        recheck_pending=market_config.resolve_requested_at(db) is not None,
        last_run=None
        if last is None
        else LastRunOut(
            kind=last.kind,
            result=last.result,
            started_at=last.started_at,
            finished_at=last.finished_at,
            counts={k: v for k, v in (last.counts or {}).items() if k in SHOWN_COUNTS},
            errors=last.errors or {},
        ),
        engines=[EngineChecksOut(name=name, checks=checks) for name, checks in engines.items()],
    )
