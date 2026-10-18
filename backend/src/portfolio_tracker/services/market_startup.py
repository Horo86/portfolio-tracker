"""Market data at startup and shutdown (D-121, D-123, D-132), after the migrations.

1. Runs left `RUNNING` by a process that ended are marked `INTERRUPTED`.
2. **Seeding:** while nothing is stored, the environment's engine lists and exchanges are
   stored; an invalid variable then stops the start, naming it. Once a configuration is
   stored, the database wins: one log line says when the environment differs (or is not
   valid) and names the reset command. Both happen with `PT_MARKET_DATA=off` too: they are
   local, and the stored configuration stays editable.
3. The loop starts. With market data on, it has the market job, with the engines' registry
   (one throttle per engine for the life of the app, its waits ending at shutdown) and the
   real HTTP transport; a missed daily run is queued as a catch-up, which never delays the
   start (it runs on the loop's thread). With `PT_MARKET_DATA=off` the loop only does the
   housekeeping (the expired-session cleanup): no market job, no transport, no outbound call
   (D-123).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine

from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.sessions import SessionPolicy
from portfolio_tracker.config import Settings
from portfolio_tracker.credentials import CredentialCipher
from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata import frankfurter
from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.http import Transport, UrllibTransport
from portfolio_tracker.marketdata.registry import BuiltEngines, Registry, default_registry
from portfolio_tracker.marketdata.routing import (
    ProviderConfigError,
    RoutingConfig,
    parse_exchanges,
    parse_provider_order,
)
from portfolio_tracker.repositories import job_runs, market_config
from portfolio_tracker.services.scheduler import (
    Scheduler,
    build_engines,
    catch_up_due,
    market_job,
    stop_aware_sleep,
)

log = logging.getLogger(__name__)

RESET_COMMAND = "python -m portfolio_tracker reset-market-data-config"
# How long shutdown waits for the loop: a call already on the network ends within its HTTP
# timeout (15 s); every other wait ends at once.
STOP_TIMEOUT_SECONDS = 20.0
# The environment variables of the engine orders (D-132), with their role and setting.
ORDER_VARIABLES = (
    ("PT_PRICE_PROVIDERS", Role.ETF_STOCK, "price_providers"),
    ("PT_BOND_PRICE_PROVIDERS", Role.BOND, "bond_price_providers"),
    ("PT_FX_PROVIDERS", Role.FX, "fx_providers"),
    ("PT_CLASSIFY_PROVIDERS", Role.CLASSIFY, "classify_providers"),
)


@dataclass
class MarketData:
    """What the app keeps: the loop and, while market data is on, what builds the engines
    for an asset search (the registry's throttles are shared with the jobs, D-128)."""

    scheduler: Scheduler
    registry: Registry | None
    transport: Transport | None = None
    cipher: CredentialCipher | None = None
    urls: dict[str, str] = field(default_factory=dict)

    @property
    def on(self) -> bool:
        return self.registry is not None

    def engines(self, engine: Engine) -> tuple[BuiltEngines, tuple[str, ...]]:
        """The engines of the stored configuration now; only while market data is on."""
        if self.registry is None or self.transport is None or self.cipher is None:
            raise RuntimeError("market data is off")
        return build_engines(engine, self.registry, self.transport, self.cipher, self.urls)

    def stop(self) -> bool:
        return self.scheduler.stop(STOP_TIMEOUT_SECONDS)


def environment_config(registry: Registry, settings: Settings) -> RoutingConfig:
    """The configuration the environment gives (every listed engine on). An invalid value
    raises `ProviderConfigError` naming its variable."""
    for variable, role, name in ORDER_VARIABLES:
        try:
            registry.check_order(role, parse_provider_order(getattr(settings, name)))
        except ProviderConfigError as error:
            raise ProviderConfigError(f"{variable}: {error}") from None
    try:
        parse_exchanges(settings.price_exchanges)
    except ProviderConfigError as error:
        raise ProviderConfigError(f"PT_PRICE_EXCHANGES: {error}") from None
    return registry.initial_config(
        price_providers=settings.price_providers,
        bond_price_providers=settings.bond_price_providers,
        fx_providers=settings.fx_providers,
        classify_providers=settings.classify_providers,
        exchanges=settings.price_exchanges,
    )


def differences(stored: RoutingConfig, wanted: RoutingConfig) -> list[str]:
    """What differs between two configurations: `on/off`, a role, `exchanges`."""
    found = ["on/off"] if stored.enabled != wanted.enabled else []
    found += [role.value for role in Role if stored.order(role) != wanted.order(role)]
    if stored.exchanges != wanted.exchanges:
        found.append("exchanges")
    return found


def seed_or_compare(engine: Engine, registry: Registry, settings: Settings) -> None:
    wanted: RoutingConfig | None = None
    problem: ProviderConfigError | None = None
    try:
        wanted = environment_config(registry, settings)
    except ProviderConfigError as error:
        problem = error
    with db_transaction(engine, write=True) as session:
        stored = market_config.load(session)
        if stored is None:
            if wanted is None:
                raise problem or ProviderConfigError("no market data configuration")
            market_config.save(session, wanted)
            log.info("market data configuration stored from the environment")
            return
    if wanted is None:
        log.warning(
            "market data environment variables are not valid; the stored configuration is used",
            extra={"problem": str(problem), "reset_command": RESET_COMMAND},
        )
    elif found := differences(stored, wanted):
        log.info(
            "market data configuration differs from the environment; the stored one is used",
            extra={"differs": found, "reset_command": RESET_COMMAND},
        )


def housekeeping(
    engine: Engine, policy: SessionPolicy, clock: Callable[[], datetime]
) -> Callable[[], None]:
    """The loop's daily local work: delete expired sessions (M2 note, D-121)."""

    def run() -> None:
        with db_transaction(engine, write=True) as session:
            removed = sessions.delete_expired(session, clock(), policy)
        if removed:
            log.info("expired sessions removed", extra={"count": removed})

    return run


def start_market_data(
    settings: Settings,
    engine: Engine,
    cipher: CredentialCipher,
    clock: Callable[[], datetime],
    session_policy: SessionPolicy,
    transport: Transport | None = None,
    registry: Registry | None = None,
) -> MarketData:
    """Prepare market data after the migrations and start the loop (housekeeping only when
    market data is off)."""
    with db_transaction(engine, write=True) as session:
        interrupted = job_runs.interrupt_running(session, clock())
    if interrupted:
        log.warning("market data runs interrupted by the last stop", extra={"runs": interrupted})
    stopping = threading.Event()
    registry = registry or default_registry(sleep=stop_aware_sleep(stopping))
    seed_or_compare(engine, registry, settings)
    cleanup = housekeeping(engine, session_policy, clock)
    if not settings.market_data:
        log.info("market data is off (PT_MARKET_DATA=off): no outbound call")
        scheduler = Scheduler(None, settings.daily_at, clock, stopping, cleanup)
        scheduler.start()
        return MarketData(scheduler, None)
    transport = transport or UrllibTransport()
    urls = {frankfurter.NAME: settings.fx_url}
    job = market_job(engine, registry, transport, cipher, urls=urls, clock=clock)
    scheduler = Scheduler(job, settings.daily_at, clock, stopping, cleanup)
    if catch_up_due(engine, clock(), settings.daily_at):
        scheduler.trigger("CATCH_UP")
    scheduler.start()
    return MarketData(scheduler, registry, transport, cipher, urls)
