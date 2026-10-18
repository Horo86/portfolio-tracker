# ADR-0001: Backend language and framework

Status: accepted (M0, October 2026)

## Context
The backend carries the calculation engine, the API, authentication, imports, the scheduler and backup. Requirements (`../requirements.md` § Non-functional, § Architecture Constraints): exact decimal arithmetic, OpenAPI, image < 200 MB, idle memory < 256 MB, startup < 10 s, one container, AGPL-compatible dependencies, maintainability by a small community. The maintainer decided the stack together with the project lead (D-53).

## Options considered
- **Python + FastAPI** — `decimal.Decimal` in the standard library (exact, configurable rounding, half-even); OpenAPI generated from type hints; mature libraries for every later need (Argon2, TOTP, xlsx reading, scheduling); small idle footprint with one Uvicorn worker; very large contributor base.
- **Java / Spring Boot** — rejected by the maintainer: JVM memory and image size conflict with the < 256 MB / < 200 MB targets.
- **Go** — rejected by the maintainer: no standard decimal type (third-party only), less convenient for spreadsheet/financial code, smaller pool of likely contributors for this domain.
- **Node.js backend** — rejected by the maintainer: no native decimal type (every amount through a library), weaker fit for a calculation-heavy engine.

## Decision
- **Python 3.12** (security-supported until October 2028; base image `python:3.12.15-slim-trixie`). Upgrade to a newer minor line is a routine change tracked in a future ADR before 2028.
- **FastAPI 0.143.0** on **Uvicorn 0.54.0** (single worker), **Pydantic 2.14.0**, **pydantic-settings 2.15.0** for environment configuration, **python-json-logger 4.2.0** for structured logs on stdout.
- Package management with **uv** (0.13.0, lockfile `backend/uv.lock`); lint and format with **ruff 0.17.0**; tests with **pytest 9.1.1** (+ **httpx2 2.13.1** for the test client; Starlette 1.7 deprecates plain httpx in its test client). Property tests use **hypothesis 6.168.2** (dev only, MPL-2.0; added in M1, D-54).
- Libraries planned for later milestones, **not added in M0**: argon2-cffi 25.1.0 (passwords, M2), pyotp 2.10.0 (TOTP, M6), openpyxl 3.1.5 (Directa xlsx, M3), APScheduler 3.11.3 (daily jobs, M4; *dropped in M4 for a standard-library loop, ADR-0006, D-121*); cryptography (credential store, M4, D-124). Versions are re-checked when they are added.

## Consequences
- Easier: exact decimals without extra libraries; OpenAPI for free; one process to deploy.
- Harder: Python is slower than JVM/Go for heavy loops — acceptable at the reference size (10 users, 5,000 transactions each); the engine must avoid per-request full recomputation where caching is cheap (M1/M2).
- All versions are pinned exactly in `backend/pyproject.toml` and locked in `uv.lock`.
