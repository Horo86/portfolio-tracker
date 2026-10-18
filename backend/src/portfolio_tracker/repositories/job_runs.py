"""The market data job log (D-121): one row per run, with counts and error codes only, never
an ISIN, a symbol, a name or a figure. Instance-wide: no owner. Read by the scheduler (the
last run, for the catch-up at startup) and by the administrator's status (M6)."""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import JobRun


def start_run(session: Session, kind: str, at: datetime) -> JobRun:
    run = JobRun(kind=kind, started_at=at, result="RUNNING")
    session.add(run)
    session.flush()
    return run


def finish_run(
    session: Session,
    run_id: UUID,
    *,
    result: str,
    at: datetime,
    counts: Mapping[str, int],
    errors: Mapping[str, int],
) -> None:
    run = session.get(JobRun, run_id)
    if run is None:
        raise LookupError("unknown job run")
    run.result, run.finished_at = result, at
    run.counts, run.errors = dict(counts), dict(errors)
    session.flush()


def interrupt_running(session: Session, at: datetime) -> int:
    """Mark the runs left `RUNNING` by a process that ended (a crash or a kill) as
    `INTERRUPTED`; called at startup, before the loop runs anything. Returns how many."""
    marked = session.execute(
        update(JobRun)
        .where(JobRun.result == "RUNNING")
        .values(result="INTERRUPTED", finished_at=at)
    )
    return marked.rowcount or 0


def last_run(session: Session) -> JobRun | None:
    """The latest run that finished, whatever its result."""
    query = (
        select(JobRun)
        .where(JobRun.finished_at.is_not(None))
        .order_by(JobRun.started_at.desc())
        .limit(1)
    )
    return session.scalars(query).one_or_none()
