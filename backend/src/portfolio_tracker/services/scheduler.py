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
run does not swallow it: the run ends as `INTERRUPTED` and the thread returns. A call already
on the network ends within its HTTP timeout; the thread is a daemon, so a process killed
meanwhile is never kept alive by it.

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
from portfolio_tracker.marketdata.registry import Registry
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.repositories import market_config
from portfolio_tracker.repositories.credentials import credential_lookup
from portfolio_tracker.services.market_jobs import Stopped, run_job

log = logging.getLogger(__name__)

# The longest single wait of the loop: the next run time is computed again after it, so a
# change of the system clock is noticed within this many seconds.
MAX_WAIT_SECONDS = 300.0


class Trigger(StrEnum):
    QUEUED = "QUEUED"
    ALREADY_QUEUED = "ALREADY_QUEUED"  # a waiting job covers it: every job does the whole run
    BUSY = "BUSY"  # "run now" while a job is running (D-121)


def next_daily(now: datetime, at: time) -> datetime:
    """The first run time at `at` (UTC) not before `now`."""
    today = datetime.combine(now.astimezone(UTC).date(), at, tzinfo=UTC)
    return today if today >= now else today + timedelta(days=1)


def stop_aware_sleep(stopping: threading.Event) -> Callable[[float], None]:
    """A sleep for the provider throttles that ends at once, raising `Stopped`, on shutdown."""

    def sleep(seconds: float) -> None:
        if stopping.wait(seconds):
            raise Stopped

    return sleep


class Scheduler:
    def __init__(
        self,
        job: Callable[[str], object],
        daily_at: time,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        stopping: threading.Event | None = None,
    ) -> None:
        self._job = job
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
        with self._lock:
            if kind == "MANUAL" and self._running is not None:
                return Trigger.BUSY
            if self._waiting is not None:
                return Trigger.ALREADY_QUEUED
            self._waiting = kind
        self._wake.set()
        return Trigger.QUEUED

    def step(self) -> str | None:
        """One turn of the loop: run the waiting job, or the daily one when due. Returns the
        kind run, None when there was nothing to do."""
        kind = self._take()
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

    def _take(self) -> str | None:
        with self._lock:
            now = self._clock()
            if now >= self._next_daily:
                # Missed days are not run one by one: one run fetches everything since the
                # last stored days (D-122).
                self._next_daily = next_daily(now + timedelta(microseconds=1), self._daily_at)
                if self._waiting is None:
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
        with db_transaction(engine, write=False) as session:
            config = market_config.load(session) or RoutingConfig(frozenset())
            built = registry.build(config, transport, credential_lookup(session, cipher), urls)
        registry.reset_throttles()
        return run_job(engine, built, config.exchanges, kind, clock)

    return job
