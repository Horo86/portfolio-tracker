# ADR-0006: Background jobs and scheduler

Status: accepted (M0, October 2026) — design only, built in M4

## Context
Daily price and FX updates and back-fill for new assets, inside the single container (`../requirements.md` § Market data; `../domain-rules.md` §12). No extra infrastructure.

## Options considered
- **APScheduler in the application process** — cron-like triggers, no broker, starts and stops with the FastAPI lifespan.
- **Celery / RQ with Redis** — a broker container: rejected (extra infrastructure).
- **System cron in the container** — a second process supervisor and environment duplication.

## Decision
- **APScheduler 3.11.3** (version re-checked in M4), one in-process scheduler started in the FastAPI lifespan, with a single Uvicorn worker so jobs never run twice.
- Jobs are idempotent and resumable (the next run back-fills missing days); schedule time configurable by environment variable; provider failures are logged and never block requests (D-16).
- Long jobs run in a worker thread so the API stays responsive.

## Consequences
- Easier: no broker, nothing to deploy.
- Harder: horizontal scaling to several app processes would require a job lock — out of scope for a household instance.
