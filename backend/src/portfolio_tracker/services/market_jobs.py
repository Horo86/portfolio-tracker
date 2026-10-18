"""One market data job (D-121, D-132, D-134): the unit of work the queue runs, one at a time.

A run records itself in the job log, then: takes up a pending re-check request (the job kind
`RESOLVE`, or a request recorded by a configuration change or the reset command), in which
case inferred classes are asked of the classifiers again and engines whose last search
accepted a listing are searched again; links new assets and walks every linked instrument
(`market_data.refresh_prices`: listings, closes with back-fill, switches); updates the FX
rates. The job log holds counts and error codes only. A run never raises an error: an
unexpected one ends it as `FAILED`, logged with its type only (its message could hold an
ISIN). Only a shutdown (`Stopped`, raised from a throttle's wait) goes through: the run is
recorded as `INTERRUPTED`, and does not count against a pending re-check request.

**The re-check request** is cleared by the run that took it up only when that run completed
the re-check: it did not fail, postponed no instrument, and no classifier or engine search
failed. Otherwise it stays pending for the next run, up to `RECHECK_MAX_RUNS` runs in a row;
the last of them clears it with a warning and records it in the job log, so a provider that
keeps failing cannot make every run a re-check. A request made while a run is going (the
stored request changed since the run read it) stays pending with its count starting afresh.

The scheduler, the queue and the triggers (startup catch-up, imports, the administrator's
"run now") come in step 10; the engines are built by the caller from the stored
configuration (`Registry.build`), so a run always uses the configuration of its start.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import JOB_KINDS
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.repositories import job_runs, market_config
from portfolio_tracker.services.fx_rates import update_fx
from portfolio_tracker.services.market_data import recheck_classes, refresh_prices

log = logging.getLogger(__name__)

# Runs in a row that may take up a re-check request without completing it (D-132).
RECHECK_MAX_RUNS = 7


class Stopped(BaseException):
    """The app is shutting down (`scheduler.stop_aware_sleep`). A `BaseException`, so the
    `except Exception` that contains one engine's failure lets it through."""


@dataclass
class JobSummary:
    """What one run did: counts and error codes only."""

    id: UUID
    kind: str
    result: str = "RUNNING"
    recheck: bool = False
    recheck_done: bool = False  # the re-check postponed nothing and no search failed
    counts: Counter[str] = field(default_factory=Counter)
    errors: Counter[str] = field(default_factory=Counter)


def run_job(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: tuple[str, ...],
    kind: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> JobSummary:
    """Run one market data job of `kind` and record it in the job log. `clock` is injectable
    for tests (no real time, no sleep)."""
    if kind not in JOB_KINDS:
        raise ValueError(f"unknown job kind {kind!r}")
    now = clock()
    with db_transaction(engine, write=True) as session:
        run = job_runs.start_run(session, kind, now)
        requested = market_config.resolve_requested_at(session)
    summary = JobSummary(run.id, kind, recheck=kind == "RESOLVE" or requested is not None)
    try:
        _work(engine, engines, exchanges, now, summary)
    except Stopped:
        summary.result = "INTERRUPTED"
        _finish(engine, summary, None, clock)
        raise
    except Exception as error:  # a run never raises: the queue goes on (D-121)
        summary.result = "FAILED"
        summary.errors[type(error).__name__] += 1
        log.error("market data job failed", extra={"kind": kind, "type": type(error).__name__})
    else:
        summary.result = "PARTIAL" if summary.errors else "OK"
    _finish(engine, summary, requested, clock)
    return summary


def _finish(
    engine: Engine,
    summary: JobSummary,
    requested: datetime | None,
    clock: Callable[[], datetime],
) -> None:
    with db_transaction(engine, write=True) as session:
        if requested is not None:
            _settle_request(session, requested, summary)
        summary.counts = +summary.counts  # the log keeps what happened
        job_runs.finish_run(
            session,
            summary.id,
            result=summary.result,
            at=clock(),
            counts=summary.counts,
            errors=summary.errors,
        )


def _settle_request(session: Session, requested: datetime, summary: JobSummary) -> None:
    """Clear the request this run read, or keep it for the next run (bounded)."""
    if summary.result != "FAILED" and summary.recheck_done:
        market_config.clear_resolve_request(session, requested)
        return
    runs = market_config.count_unfinished_resolve(session, requested)
    if runs is None:
        return  # replaced by a request made during the run: it starts afresh
    if runs < RECHECK_MAX_RUNS:
        summary.counts["recheck_pending"] += 1
        return
    market_config.clear_resolve_request(session, requested)
    summary.counts["recheck_abandoned"] += 1
    log.warning("listing re-check abandoned", extra={"runs": runs})


def _work(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: tuple[str, ...],
    now: datetime,
    summary: JobSummary,
) -> None:
    counts, errors = summary.counts, summary.errors
    classified = {}
    recheck_errors: Counter[str] = Counter()
    if summary.recheck:
        classified = recheck_classes(engine, engines, counts, recheck_errors)
        errors.update(recheck_errors)
    resolved, closes, switched = refresh_prices(
        engine, engines, exchanges, now, recheck=summary.recheck, classified=classified
    )
    summary.recheck_done = not (recheck_errors or resolved.errors or resolved.postponed)
    counts.update(
        {
            "assets_linked": resolved.linked,
            "instruments_created": resolved.created,
            "listings_chosen": resolved.chosen,
            "not_found": resolved.not_found,
            "postponed": resolved.postponed,
            "no_engine": resolved.no_engine,
            "classified": sum(resolved.classified.values()),
            "targets": len(resolved.targets),
            "series_updated": closes.updated,
            "closes_stored": closes.stored,
            "closes_mismatch": closes.mismatch,
            "switched": switched.switched,
            "level_refused": switched.level_refused,
            "switch_waiting": switched.waiting,
        }
    )
    errors.update(resolved.errors)
    errors.update(closes.errors)
    fx = update_fx(engine, engines.fx(), now.date())
    counts["fx_currencies_updated"] = len(fx.stored)
    counts["fx_rates_stored"] = sum(fx.stored.values())
    errors.update(Counter(fx.failed.values()))
