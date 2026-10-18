"""One market data job (D-121, D-132, D-134): the unit of work the queue runs, one at a time.

A run records itself in the job log, then: takes up a pending re-check request (the job kind
`RESOLVE`, or a request recorded by a configuration change or the reset command), in which
case inferred classes are asked of the classifiers again and engines whose last search
accepted a listing are searched again; links new assets and walks every linked instrument
(`market_data.refresh_prices`: listings, closes with back-fill, switches); updates the FX
rates. The job log holds counts and error codes only. A run never raises: an unexpected
error ends it as `FAILED`, logged with its type only (its message could hold an ISIN).

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

from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import JOB_KINDS
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.repositories import job_runs, market_config
from portfolio_tracker.services.fx_rates import update_fx
from portfolio_tracker.services.market_data import recheck_classes, refresh_prices

log = logging.getLogger(__name__)


@dataclass
class JobSummary:
    """What one run did: counts and error codes only."""

    id: UUID
    kind: str
    result: str = "RUNNING"
    recheck: bool = False
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
    except Exception as error:  # a run never raises: the queue goes on (D-121)
        summary.result = "FAILED"
        summary.errors[type(error).__name__] += 1
        log.error("market data job failed", extra={"kind": kind, "type": type(error).__name__})
    else:
        summary.result = "PARTIAL" if summary.errors else "OK"
    with db_transaction(engine, write=True) as session:
        if summary.recheck and summary.result != "FAILED":
            market_config.clear_resolve_request(session, now)  # later requests stay pending
        job_runs.finish_run(
            session,
            summary.id,
            result=summary.result,
            at=clock(),
            counts=summary.counts,
            errors=summary.errors,
        )
    return summary


def _work(
    engine: Engine,
    engines: BuiltEngines,
    exchanges: tuple[str, ...],
    now: datetime,
    summary: JobSummary,
) -> None:
    counts, errors = summary.counts, summary.errors
    if summary.recheck:
        rechecked = recheck_classes(engine, engines)
        for name in ("reclassified", "deactivated"):
            counts[name] += rechecked.pop(name, 0)
        errors.update(rechecked)
    resolved, closes, switched = refresh_prices(
        engine, engines, exchanges, now, recheck=summary.recheck
    )
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
    for name in [name for name, value in counts.items() if not value]:
        del counts[name]  # the log keeps what happened
