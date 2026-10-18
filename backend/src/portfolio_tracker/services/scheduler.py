"""The market data loop (D-121, ADR-0006): one background thread, one queue, one job at a time.

The loop runs a job once a day at a configured UTC time, and whenever a trigger queues one
(the startup catch-up, the back-fill after an import, the administrator's "run now", a
listing re-check). Every job does the whole run (`market_jobs.run_job`), so **at most one job
waits** behind the running one: a trigger while one is already waiting is already covered.
The administrator's "run now" (`MANUAL`) is refused while a job is running; any other
trigger is queued behind it.

**Stopping.** `stop()` sets an event that ends every wait at once: the loop's own wait, and
the waits of the provider throttles (`stop_aware_sleep`), which raise `Stopped`. `Stopped` is
a `BaseException`, so the `except Exception` that keeps one engine's failure from stopping a
run does not swallow it: the run ends as `INTERRUPTED` and the thread returns. The throttles
also refuse any new call once stopping. A call already on the network ends only within its
response deadline (up to about a minute); the thread is a daemon, so a process killed
meanwhile is never kept alive by it.

**Catch-up** (`catch_up_due`): at startup, a run is queued when no run completed (`OK` or
`PARTIAL`) since the latest daily run time that has passed; an empty job log counts as
missed. **A listing re-check** is always asked through the stored request (D-132,
`request_recheck`), which the next run takes up, so a request is never lost with the queue.

**Housekeeping** (the expired-session cleanup) runs at the loop's first turn and at each
daily time, whether market data is on or not. With market data off the loop has no job: it
runs the housekeeping only, queues nothing and makes no outbound call (D-123).

Times are UTC and come from an injectable clock; `step()` does one turn of the loop without
waiting, so tests drive the loop with a fake clock and never sleep.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum

from sqlalchemy import Engine

from portfolio_tracker.credentials import CredentialCipher
from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata.http import Transport
from portfolio_tracker.marketdata.registry import BuiltEngines, Registry
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.marketdata.throttle import Stopped
from portfolio_tracker.repositories import job_runs, market_config
from portfolio_tracker.repositories.credentials import credential_lookup
from portfolio_tracker.services.market_jobs import run_job

log = logging.getLogger(__name__)

# The longest single wait of the loop: the next run time is computed again after it, so a
# change of the system clock is noticed within this many seconds.
MAX_WAIT_SECONDS = 300.0


class Trigger(StrEnum):
    QUEUED = "QUEUED"
    ALREADY_QUEUED = "ALREADY_QUEUED"  # a waiting job covers it: every job does the whole run
    BUSY = "BUSY"  # "run now" while a job is running (D-121)
    OFF = "OFF"  # market data is off: no job to run


def next_daily(now: datetime, at: time) -> datetime:
    """The first run time at `at` (UTC) not before `now`."""
    today = datetime.combine(now.astimezone(UTC).date(), at, tzinfo=UTC)
    return today if today >= now else today + timedelta(days=1)


def last_daily(now: datetime, at: time) -> datetime:
    """The latest run time at `at` (UTC) not after `now`."""
    upcoming = next_daily(now, at)
    return upcoming if upcoming == now else upcoming - timedelta(days=1)


def catch_up_due(engine: Engine, now: datetime, at: time) -> bool:
    """Whether the daily run since the latest run time was missed (D-121): no run completed
    since then. A failed or interrupted run, or an empty job log, counts as missed."""
    with db_transaction(engine, write=False) as session:
        completed = job_runs.last_completed_start(session)
    return completed is None or completed < last_daily(now, at)


def stop_aware_sleep(stopping: threading.Event) -> Callable[[float], None]:
    """A sleep for the provider throttles that ends at once, raising `Stopped`, on shutdown."""

    def sleep(seconds: float) -> None:
        if stopping.wait(seconds):
            raise Stopped

    return sleep


class Scheduler:
    def __init__(
        self,
        job: Callable[[str], object] | None,
        daily_at: time,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        stopping: threading.Event | None = None,
        housekeeping: Callable[[], object] | None = None,
    ) -> None:
        self._job = job
        self._housekeeping = housekeeping
        self._housekeeping_due = housekeeping is not None
        self._daily_at = daily_at
        self._clock = clock
        self.stopping = stopping or threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._waiting: str | None = None
        self._running: str | None = None
        self._next_daily = next_daily(clock(), daily_at)
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> str | None:
        """The kind of the job running now, if any."""
        return self._running

    @property
    def next_daily(self) -> datetime:
        return self._next_daily

    def trigger(self, kind: str) -> Trigger:
        if self._job is None:
            return Trigger.OFF
        with self._lock:
            if kind == "MANUAL" and self._running is not None:
                return Trigger.BUSY
            if self._waiting is not None:
                return Trigger.ALREADY_QUEUED
            self._waiting = kind
        self._wake.set()
        return Trigger.QUEUED

    def request_recheck(self, engine: Engine) -> Trigger:
        """A listing re-check (e.g. after a configuration change): recorded as the stored
        request (D-132), then a run is queued, which takes it up; a request made while a job
        runs stays pending for the queued one."""
        with db_transaction(engine, write=True) as session:
            market_config.request_resolve(session, self._clock())
        return self.trigger("RESOLVE")

    def step(self) -> str | None:
        """One turn of the loop: run the waiting job, or the daily one when due. Returns the
        kind run, None when there was nothing to do. The housekeeping, when due, comes first."""
        kind = self._take()
        if self._housekeeping_due:
            self._housekeeping_due = False
            self._housekeep()
        if kind is None:
            return None
        try:
            self._job(kind)
        except Stopped:
            log.info("market data job stopped by shutdown", extra={"kind": kind})
            raise
        except Exception as error:  # the loop survives any job error (D-121)
            log.error("market data job error", extra={"kind": kind, "type": type(error).__name__})
        finally:
            with self._lock:
                self._running = None
        return kind

    def _housekeep(self) -> None:
        assert self._housekeeping is not None
        try:
            self._housekeeping()
        except Exception as error:  # the loop survives it too
            log.error("housekeeping error", extra={"type": type(error).__name__})

    def _take(self) -> str | None:
        with self._lock:
            now = self._clock()
            if self._next_daily - now > timedelta(days=1):
                # The clock went back (e.g. corrected by NTP after a wrong boot time): the next
                # run is computed again rather than awaited for days (final review M-1).
                self._next_daily = next_daily(now, self._daily_at)
            if now >= self._next_daily:
                # Missed days are not run one by one: one run fetches everything since the
                # last stored days (D-122).
                self._next_daily = next_daily(now + timedelta(microseconds=1), self._daily_at)
                self._housekeeping_due = self._housekeeping is not None
                if self._waiting is None and self._job is not None:
                    self._waiting = "DAILY"
            kind, self._waiting = self._waiting, None
            self._running = kind
            return kind

    def _seconds_to_next(self) -> float:
        left = (self._next_daily - self._clock()).total_seconds()
        return max(0.0, min(left, MAX_WAIT_SECONDS))

    def _loop(self) -> None:
        try:
            while not self.stopping.is_set():
                if self.step() is None:
                    self._wake.wait(self._seconds_to_next())
                    self._wake.clear()
        except Stopped:
            pass
        log.info("market data loop stopped")

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("the scheduler is already started")
        self._thread = threading.Thread(target=self._loop, name="market-data", daemon=True)
        self._thread.start()
        log.info("market data loop started", extra={"next_run": self._next_daily.isoformat()})

    def stop(self, timeout: float) -> bool:
        """Ask the loop to stop and wait up to `timeout` seconds. Returns whether it ended."""
        self.stopping.set()
        self._wake.set()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("market data loop still busy at shutdown")
            return False
        return True

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def build_engines(
    engine: Engine,
    registry: Registry,
    transport: Transport,
    cipher: CredentialCipher,
    urls: dict[str, str] | None = None,
) -> tuple[BuiltEngines, tuple[str, ...]]:
    """The enabled engines of the stored configuration (credentials read now), and the
    preferred exchanges. Local work in one short read transaction: no network."""
    with db_transaction(engine, write=False) as session:
        config = market_config.load(session) or RoutingConfig(frozenset())
        built = registry.build(config, transport, credential_lookup(session, cipher), urls)
    return built, config.exchanges


def market_job(
    engine: Engine,
    registry: Registry,
    transport: Transport,
    cipher: CredentialCipher,
    *,
    urls: dict[str, str] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Callable[[str], object]:
    """The job the loop runs: the engines built from the stored configuration of this run's
    start (credentials read then), the throttles' exhaustion reset ("until the next run"),
    then `run_job`."""

    def job(kind: str) -> object:
        built, exchanges = build_engines(engine, registry, transport, cipher, urls)
        registry.reset_throttles()
        return run_job(engine, built, exchanges, kind, clock)

    return job
