# ADR-0006: Background jobs and scheduler

Status: accepted (M0, October 2026) — **amended in M4** (D-121): a standard-library loop replaces APScheduler

## Context
Daily price and FX updates and back-fill for new assets, inside the single container (`../requirements.md` § Market data; `../domain-rules.md` §12). No extra infrastructure.

## Options considered
- **APScheduler in the application process** — cron-like triggers, no broker, starts and stops with the FastAPI lifespan.
- **Celery / RQ with Redis** — a broker container: rejected (extra infrastructure).
- **System cron in the container** — a second process supervisor and environment duplication.
- **A standard-library loop in a background thread** (added in M4) — one daily job, a queue and a lock; no dependency.

## Decision
- *M0:* APScheduler 3.11.3, one in-process scheduler started in the FastAPI lifespan, with a single Uvicorn worker so jobs never run twice.
- **M4 amendment (D-121):** there is only one daily job, so APScheduler adds a dependency without benefit. Instead:
  - one background thread started and stopped by the FastAPI lifespan (single Uvicorn worker, as before), running once a day at `PT_MARKET_DATA_TIME` (UTC, default `22:00`, D-123); not started at all when `PT_MARKET_DATA=off`;
  - an **injectable clock and sleeper**, so tests advance time without sleeping;
  - **catch-up at startup** when the last run recorded in the job log is older than the last scheduled time;
  - **one queue, one job at a time**: the daily update, the catch-up and the back-fill of newly linked assets share it and the provider throttle; an administrator trigger is refused while a run is in progress;
  - jobs survive exceptions (logged by type, recorded in the job log) and the thread stops cleanly on shutdown;
  - network calls happen **outside** database transactions; results are written in short write transactions per series, so the SQLite write lock is never held while waiting on a provider;
  - the same loop deletes expired sessions.
- **Built in M4 step 10** (`services/scheduler.py`): every job does the whole run, so at most one job waits behind the running one; the administrator's "run now" is refused while a job runs, other triggers wait. The loop waits at most 5 minutes at a time, then computes the next run time again, so a change of the system clock is noticed. Shutdown sets one event that ends the loop's wait and the provider throttles' waits (they raise `Stopped`, a `BaseException` that the per-engine error handling does not catch); the run is recorded as `INTERRUPTED`, a call already on the network ends within its HTTP timeout, and the thread is a daemon. Runs left `RUNNING` by a crash are marked `INTERRUPTED` at the next start. Each run builds the engines from the stored configuration and resets the throttles' failure counts.
- Jobs are idempotent and resumable: every fetch starts from the last stored days of its series, the last 5 fetched again (D-122); provider failures are logged and never block requests (D-16).

## Consequences
- Easier: no broker and no scheduler dependency; deterministic tests with a fake clock.
- Harder: horizontal scaling to several app processes would require a job lock — out of scope for a household instance. Cron-like schedules beyond "once a day" would need more code (not needed in V1).
