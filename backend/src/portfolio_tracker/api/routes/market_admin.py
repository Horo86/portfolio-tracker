"""Market data for the Administrator (D-121, D-135, D-186, D-188): start a run now, the
status, and the price sources: each engine's state, an engine on or off, the order per role
and the preferred exchanges (no step-up, D-187; the credential and the purge come later).
Administrator only: 404 for everyone else (route matrix). Responses carry counts and error
codes only, never an ISIN, an instrument, a listing or a symbol, which would show what users
hold (D-116).

The last run's counts are shown only from `SHOWN_COUNTS` (final review M-2, D-135): a count
that changes when an instrument is new (created, classified, searched, back-filled) would tell
an Administrator who adds an asset and runs now whether another user already holds that
security. They stay in the server's job log. The per-engine totals remain: an accepted
residual (D-135).
"""

from collections.abc import Callable
from datetime import datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import AdminAuth, DbSession, Now, ReadAdminAuth, transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import ErrorCode, Role
from portfolio_tracker.marketdata.routing import ProviderConfigError
from portfolio_tracker.repositories import audit, instruments, job_runs, market_config
from portfolio_tracker.services import price_sources
from portfolio_tracker.services.price_sources import EngineState, EngineStatus
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
    # Per role, the enabled engines in their configured order (D-175 (4)): engine names only,
    # for the read-only card; a role may be empty (bonds: manual prices).
    routes: dict[Role, list[str]]


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
    """Counts only (D-135): the last run's counts and error codes, per engine how many
    instruments its last search left in each status, and the enabled engines of each role in
    their order (D-175 (4))."""
    market_data = request.app.state.market_data
    scheduler = market_data.scheduler
    last = job_runs.last_run(db)
    engines: dict[str, dict[str, int]] = {}
    for (provider, check_status), count in sorted(instruments.check_counts(db).items()):
        engines.setdefault(provider, {})[check_status] = count
    config = market_config.load(db)  # None until seeded: no engine in any role
    routes = {role: list(config.active(role)) if config else [] for role in Role}
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
        routes=routes,
    )


# --- the price sources (D-135, D-186, D-188) ---------------------------------------------

MarketDataState = Literal["ON", "OFF"]


class CapabilitiesOut(BaseModel):
    search_isin: bool
    search_text: bool
    echoes_isin: bool
    units: list[QuoteUnit]
    history_years: int | None


class EngineOut(BaseModel):
    """One engine: what it declares, whether it is on, its credential's state (never the
    value), its health since the start of the process, and instrument counts by the status of
    its last search. `status` is computed for a disabled engine too (D-188)."""

    name: str
    roles: list[Role]
    capabilities: CapabilitiesOut
    official: bool
    terms_url: str | None
    enabled: bool
    off_by_environment: bool  # PT_MARKET_DATA=off: no engine is called
    needs_credential: bool
    credential_label: str | None
    credential_set: bool
    credential_updated_at: datetime | None
    status: EngineStatus
    last_success_at: datetime | None
    last_error_code: ErrorCode | None
    last_error_at: datetime | None
    paused_until: datetime | None
    checks: dict[str, int]

    @classmethod
    def of(cls, state: EngineState, market_data_on: bool) -> "EngineOut":
        spec, capabilities, health = state.spec, state.spec.capabilities, state.health
        return cls(
            name=spec.name,
            roles=sorted(capabilities.roles, key=list(Role).index),
            capabilities=CapabilitiesOut(
                search_isin=capabilities.search_isin,
                search_text=capabilities.search_text,
                echoes_isin=capabilities.echoes_isin,
                units=sorted(capabilities.units),
                history_years=capabilities.history_years,
            ),
            official=capabilities.official,
            terms_url=capabilities.terms_url,
            enabled=state.enabled,
            off_by_environment=not market_data_on,
            needs_credential=capabilities.needs_credential,
            credential_label=capabilities.credential_label,
            credential_set=state.credential_updated_at is not None,
            credential_updated_at=state.credential_updated_at,
            status=state.status,
            last_success_at=health.last_success_at,
            last_error_code=health.last_error_code,
            last_error_at=health.last_error_at,
            paused_until=health.paused_until,
            checks=state.checks,
        )


class EnginesOut(BaseModel):
    market_data: MarketDataState
    engines: list[EngineOut]


class RoutesOut(BaseModel):
    """The stored order of each role, disabled engines in their place (a stored name this
    version has no adapter for is shown as stored); the engines that declare each role; and
    the preferred exchanges."""

    market_data: MarketDataState
    routes: dict[Role, list[str]]
    available: dict[Role, list[str]]
    exchanges: list[str]


class EngineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


EngineName = Annotated[str, StringConstraints(max_length=64)]
ExchangeName = Annotated[str, StringConstraints(max_length=128)]


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engines: list[EngineName] = Field(max_length=16)


class ExchangesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exchanges: list[ExchangeName] = Field(max_length=20)


_VALIDATION = {422: {"description": "REQUEST_INVALID, VALIDATION_FAILED"}}


def _state(request: Request) -> MarketDataState:
    return "ON" if request.app.state.market_data.on else "OFF"


def _engines_out(request: Request, db: Session, names: tuple[str, ...]) -> list[EngineOut]:
    market_data = request.app.state.market_data
    states = price_sources.engine_states(db, market_data.registry, names)
    return [EngineOut.of(state, market_data.on) for state in states]


def _routes_out(request: Request, db: Session) -> RoutesOut:
    registry = request.app.state.market_data.registry
    config = price_sources.stored(db)
    return RoutesOut(
        market_data=_state(request),
        routes={role: list(config.order(role)) for role in Role},
        available={role: list(registry.names_for(role)) for role in Role},
        exchanges=list(config.exchanges),
    )


def _write(request: Request, change: Callable[[Session], bool]) -> None:
    """One write transaction for the change; once it is committed, a change queues the run
    that takes up the re-check it recorded (D-134). While market data is off, the request
    stays stored for the first run (D-132)."""
    try:
        with transaction(request.app, write=True) as db:
            changed = change(db)
    except ProviderConfigError as error:
        raise ApiError(422, "VALIDATION_FAILED", str(error)) from None
    if changed:
        request.app.state.market_data.scheduler.trigger("RESOLVE")


@router.get("/engines")
def list_engines(auth: AdminAuth, request: Request, db: DbSession) -> EnginesOut:
    """Every engine of this version, by name (D-135): counts and codes only."""
    names = request.app.state.market_data.registry.names
    return EnginesOut(market_data=_state(request), engines=_engines_out(request, db, names))


@router.patch("/engines/{name}", responses={404: {"description": "NOT_FOUND"}})
def change_engine(
    name: str, body: EngineIn, auth: ReadAdminAuth, request: Request, at: Now
) -> EngineOut:
    """Turn an engine on or off. A keyed engine without its credential may be turned on: it
    reads `MISSING_CREDENTIAL` and the run skips it (D-188)."""
    registry = request.app.state.market_data.registry
    if name not in registry.names:
        raise not_found("engine")
    ip = client_ip(request)
    _write(
        request,
        lambda db: price_sources.set_enabled(
            db, registry, name, body.enabled, actor_id=auth.user.id, ip=ip, at=at
        ),
    )
    with transaction(request.app, write=False) as db:
        return _engines_out(request, db, (name,))[0]


@router.get("/routes")
def read_routes(auth: AdminAuth, request: Request, db: DbSession) -> RoutesOut:
    return _routes_out(request, db)


@router.put("/routes/{role}", responses=_VALIDATION)
def change_route(
    role: Role, body: OrderIn, auth: ReadAdminAuth, request: Request, at: Now
) -> RoutesOut:
    """Replace the order of one role: only engines that declare it, each once (D-131).
    An empty order is allowed (D-188)."""
    registry = request.app.state.market_data.registry
    ip = client_ip(request)
    _write(
        request,
        lambda db: price_sources.set_order(
            db, registry, role, body.engines, actor_id=auth.user.id, ip=ip, at=at
        ),
    )
    with transaction(request.app, write=False) as db:
        return _routes_out(request, db)


@router.put("/exchanges", responses=_VALIDATION)
def change_exchanges(
    body: ExchangesIn, auth: ReadAdminAuth, request: Request, at: Now
) -> RoutesOut:
    """Replace the preferred exchanges (D-125), in order; an empty list means no preference."""
    ip = client_ip(request)
    _write(
        request,
        lambda db: price_sources.set_exchanges(
            db, body.exchanges, actor_id=auth.user.id, ip=ip, at=at
        ),
    )
    with transaction(request.app, write=False) as db:
        return _routes_out(request, db)
