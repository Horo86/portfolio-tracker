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
  - one background thread started and stopped by the FastAPI lifespan (single Uvicorn worker, as before), running once a day at `PT_MARKET_DATA_TIME` (UTC, default `22:00`, D-123); with `PT_MARKET_DATA=off` it runs no market data job and makes no outbound call, and only deletes expired sessions (amended in step 10, D-123);
  - an **injectable clock and sleeper**, so tests advance time without sleeping;
  - **catch-up at startup** when no run completed (`OK` or `PARTIAL`) since the latest scheduled time that has passed (a failed or interrupted run, or an empty job log, counts as missed);
  - **one queue, one job at a time**: the daily update, the catch-up and the back-fill of newly linked assets share it and the provider throttle; an administrator trigger is refused while a run is in progress;
  - jobs survive exceptions (logged by type, recorded in the job log) and the thread stops cleanly on shutdown;
  - network calls happen **outside** database transactions; results are written in short write transactions per series, so the SQLite write lock is never held while waiting on a provider;
  - the same loop deletes expired sessions.
- **Built in M4 step 10** (`services/scheduler.py`): every job does the whole run, so at most one job waits behind the running one; the administrator's "run now" is refused while a job runs, other triggers wait. The loop waits at most 5 minutes at a time, then computes the next run time again, so a change of the system clock is noticed: forward, the missed run happens once; back by more than a day, the next run time is recomputed instead of awaited (final review M-1). Shutdown sets one event that ends the loop's wait and the provider throttles' waits (they raise `Stopped`, a `BaseException` that the per-engine error handling does not catch); the throttles also refuse any new provider call once stopping, even one that would not wait; the run is recorded as `INTERRUPTED`; a call already on the network ends only within its response deadline (15 s per read, 60 s per response), so shutdown waits for the loop at most 20 s and then goes on without closing the database engine under it; the thread is a daemon (final review M-4). Runs left `RUNNING` by a crash are marked `INTERRUPTED` at the next start. Each run builds the engines from the stored configuration and resets the throttles' failure counts.
- Jobs are idempotent and resumable: every fetch starts from the last stored days of its series, the last 5 fetched again (D-122); provider failures are logged and never block requests (D-16).

## Consequences
- Easier: no broker and no scheduler dependency; deterministic tests with a fake clock.
- Harder: horizontal scaling to several app processes would require a job lock — out of scope for a household instance. **Two containers on the same `/data` are not supported**: each would run its own loop and both would write to one SQLite database. The suggested Compose file sets `stop_grace_period: 30s`, so a stop usually lets a provider call in flight end before the process is killed (a slow answer can take up to its 60 s deadline, and is then cut); a run cut short anyway is marked `INTERRUPTED` at the next start and the next run continues from the last stored days. Cron-like schedules beyond "once a day" would need more code (not needed in V1).
