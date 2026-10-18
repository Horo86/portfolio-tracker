"""The market data loop (D-121, ADR-0006): the daily time, one queue and one job at a time,
errors, shutdown, and the job built from the stored configuration. A fake clock drives
`step()`; the thread tests wait on events only, never on time."""

import logging
import threading
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from engines import ETF_ISIN, NOW, FakePrices, built, holdings, listing
from fakes import FakeTransport, queue
from sqlalchemy import Engine, select

from portfolio_tracker.credentials import CredentialCipher, SecretKey
from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import (
    Close,
    CloseSeries,
    ErrorCode,
    ProviderError,
    Role,
)
from portfolio_tracker.marketdata.registry import ProviderContext, ProviderSpec, Registry
from portfolio_tracker.marketdata.routing import RoutingConfig
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy
from portfolio_tracker.repositories import job_runs, market_config
from portfolio_tracker.services.market_jobs import Stopped, run_job
from portfolio_tracker.services.scheduler import (
    MAX_WAIT_SECONDS,
    Scheduler,
    Trigger,
    market_job,
    next_daily,
    stop_aware_sleep,
)

AT = time(22, 0)
DAY = datetime(2026, 3, 6, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def recording(log: list[str], effect=None):
    def job(kind: str) -> None:
        log.append(kind)
        if effect:
            effect(kind)

    return job


# --- the daily time ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (DAY.replace(hour=9), DAY.replace(hour=22)),
        (DAY.replace(hour=22), DAY.replace(hour=22)),  # due now
        (DAY.replace(hour=22, second=1), DAY.replace(hour=22) + timedelta(days=1)),
        (DAY.replace(hour=23, minute=59), datetime(2026, 3, 7, 22, tzinfo=UTC)),
    ],
)
def test_the_next_daily_run(now: datetime, expected: datetime) -> None:
    assert next_daily(now, AT) == expected


def test_the_daily_job_runs_once_when_due_then_tomorrow() -> None:
    clock, done = Clock(DAY.replace(hour=21)), []
    scheduler = Scheduler(recording(done), AT, clock)
    assert scheduler.step() is None
    clock.now = DAY.replace(hour=22, minute=1)
    assert scheduler.step() == "DAILY" and scheduler.step() is None
    assert scheduler.next_daily == DAY.replace(hour=22) + timedelta(days=1)
    assert done == ["DAILY"]


def test_missed_days_make_one_run() -> None:
    """One run fetches everything from the last stored days (D-122)."""
    clock, done = Clock(DAY.replace(hour=21)), []
    scheduler = Scheduler(recording(done), AT, clock)
    clock.now = DAY + timedelta(days=3, hours=23)
    assert scheduler.step() == "DAILY" and scheduler.step() is None
    assert scheduler.next_daily == DAY.replace(hour=22) + timedelta(days=4)


def test_the_loop_waits_until_the_next_run_at_most_a_few_minutes() -> None:
    clock = Clock(DAY.replace(hour=21, minute=58))
    scheduler = Scheduler(recording([]), AT, clock)
    assert scheduler._seconds_to_next() == 120
    clock.now = DAY.replace(hour=9)
    assert scheduler._seconds_to_next() == MAX_WAIT_SECONDS  # a clock change is noticed


# --- one queue, one job at a time -------------------------------------------------------------


def test_a_trigger_queues_one_job_and_a_second_is_already_covered() -> None:
    clock, done = Clock(DAY.replace(hour=9)), []
    scheduler = Scheduler(recording(done), AT, clock)
    assert scheduler.trigger("CATCH_UP") is Trigger.QUEUED
    assert scheduler.trigger("BACKFILL") is Trigger.ALREADY_QUEUED
    assert scheduler.trigger("MANUAL") is Trigger.ALREADY_QUEUED
    assert scheduler.step() == "CATCH_UP" and scheduler.step() is None
    assert done == ["CATCH_UP"]


def test_run_now_is_refused_while_a_job_runs_other_triggers_wait() -> None:
    answers: list[Trigger] = []

    def during(kind: str) -> None:
        if kind == "DAILY":
            answers.append(scheduler.trigger("MANUAL"))
            answers.append(scheduler.trigger("BACKFILL"))
            answers.append(scheduler.trigger("CATCH_UP"))

    clock, done = Clock(DAY.replace(hour=22)), []
    scheduler = Scheduler(recording(done, during), AT, clock)
    assert scheduler.step() == "DAILY"
    assert answers == [Trigger.BUSY, Trigger.QUEUED, Trigger.ALREADY_QUEUED]
    assert scheduler.running is None
    assert scheduler.step() == "BACKFILL" and done == ["DAILY", "BACKFILL"]


def test_a_daily_time_reached_while_a_job_waits_is_covered_by_it() -> None:
    clock, done = Clock(DAY.replace(hour=21)), []
    scheduler = Scheduler(recording(done), AT, clock)
    scheduler.trigger("BACKFILL")
    clock.now = DAY.replace(hour=22)
    assert scheduler.step() == "BACKFILL" and scheduler.step() is None
    assert done == ["BACKFILL"]


def test_a_job_error_is_logged_and_the_loop_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    def broken(kind: str) -> None:
        raise RuntimeError(f"bad {ETF_ISIN}")

    scheduler = Scheduler(broken, AT, Clock(DAY.replace(hour=9)))
    scheduler.trigger("MANUAL")
    with caplog.at_level(logging.ERROR):
        assert scheduler.step() == "MANUAL"
    assert scheduler.running is None and scheduler.trigger("MANUAL") is Trigger.QUEUED
    text = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert "market data job error" in caplog.text and ETF_ISIN not in text


# --- the thread and shutdown ------------------------------------------------------------------


def test_a_trigger_wakes_the_thread_and_stop_ends_it() -> None:
    ran = threading.Event()
    scheduler = Scheduler(lambda kind: ran.set(), AT, Clock(DAY.replace(hour=9)))
    scheduler.start()
    try:
        assert scheduler.alive
        scheduler.trigger("MANUAL")
        assert ran.wait(5)
    finally:
        assert scheduler.stop(timeout=5)
    assert not scheduler.alive


def test_stop_ends_a_long_throttle_wait_at_once() -> None:
    """A job waits on a provider's 15-minute back-off: stopping ends it, the thread ends."""
    stopping = threading.Event()
    throttle = Throttle(ThrottlePolicy(min_interval=900), sleep=stop_aware_sleep(stopping))
    waiting, outcome = threading.Event(), []

    def job(kind: str) -> None:
        throttle.call(lambda: None)  # the first call is free
        waiting.set()
        try:
            throttle.call(lambda: None)  # waits 900 s
        except Stopped:
            outcome.append("stopped")
            raise

    scheduler = Scheduler(job, AT, Clock(DAY.replace(hour=9)), stopping)
    scheduler.start()
    scheduler.trigger("MANUAL")
    assert waiting.wait(5)
    assert scheduler.stop(timeout=5) and outcome == ["stopped"]


def test_a_stop_aware_sleep_returns_normally_when_not_stopped() -> None:
    stopping = threading.Event()
    stop_aware_sleep(stopping)(0)  # no wait, no stop
    stopping.set()
    with pytest.raises(Stopped):
        stop_aware_sleep(stopping)(0)


# --- the job log ------------------------------------------------------------------------------


def runs(engine: Engine) -> list[models.JobRun]:
    with db_transaction(engine, write=False) as session:
        return list(session.scalars(select(models.JobRun).order_by(models.JobRun.started_at)))


def test_a_run_stopped_by_shutdown_is_interrupted_and_keeps_the_request(
    db_engine: Engine,
) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    with db_transaction(db_engine, write=True) as session:
        market_config.seed_if_empty(session, RoutingConfig(frozenset()))
        market_config.request_resolve(session, NOW)

    class Shutdown(FakePrices):
        def search(self, query, *, max_wait=None):
            raise Stopped

    with pytest.raises(Stopped):
        run_job(db_engine, built([Shutdown("alpha", {})]), (), "DAILY", lambda: NOW)
    (run,) = runs(db_engine)
    assert (run.result, run.finished_at) == ("INTERRUPTED", NOW)
    with db_transaction(db_engine, write=False) as session:
        assert market_config.resolve_requested_at(session) == NOW
        assert session.get(models.MarketDataConfig, 1).resolve_attempts == 0


def test_runs_left_running_by_a_crash_are_marked_interrupted(db_engine: Engine) -> None:
    with db_transaction(db_engine, write=True) as session:
        job_runs.start_run(session, "DAILY", NOW)
        done = job_runs.start_run(session, "MANUAL", NOW + timedelta(hours=1))
        job_runs.finish_run(session, done.id, result="OK", at=NOW, counts={}, errors={})
    later = NOW + timedelta(days=1)
    with db_transaction(db_engine, write=True) as session:
        assert job_runs.interrupt_running(session, later) == 1
    assert [(r.result, r.finished_at) for r in runs(db_engine)] == [
        ("INTERRUPTED", later),
        ("OK", NOW),
    ]


# --- the job built from the stored configuration ----------------------------------------------

SERIES = CloseSeries("EUR", QuoteUnit.CURRENCY, (Close(NOW.date(), Decimal("10.30")),))


def test_the_job_uses_the_stored_configuration_and_resets_exhausted_throttles(
    db_engine: Engine,
) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    built_with: list[ProviderContext] = []
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    alpha.closes["XMPW.MI"] = SERIES

    def factory(context: ProviderContext) -> FakePrices:
        built_with.append(context)
        return alpha

    registry = Registry()
    registry.register(ProviderSpec("alpha", factory, alpha.capabilities))
    registry.register(ProviderSpec("beta", factory, alpha.capabilities))
    with db_transaction(db_engine, write=True) as session:
        market_config.seed_if_empty(
            session, RoutingConfig(frozenset({"alpha"}), {Role.ETF_STOCK: ("alpha", "beta")})
        )
    throttle = registry.throttle("alpha")
    for _ in range(throttle.policy.stop_after_failures):
        throttle._failed(ProviderError(ErrorCode.HTTP_ERROR, "500", status=500))
    assert throttle.exhausted

    transport = FakeTransport(queue())
    cipher = CredentialCipher(SecretKey(bytes(32), "test"))
    summary = market_job(db_engine, registry, transport, cipher, clock=lambda: NOW)("DAILY")

    assert not throttle.exhausted
    assert [c.throttle for c in built_with] == [throttle]  # beta is disabled: not built
    assert summary.result == "OK" and summary.counts["listings_chosen"] == 1
    assert transport.calls == []  # the fake engine never uses the network
