# Architecture

Status: **decided in M0** (D-53). Each decision is an ADR in this folder (`adr-NNNN-title.md`). Requirements and constraints are in `../requirements.md`; a change of decision is a new ADR that supersedes the old one — do not decide by habit, compare against the criteria.

## Decision criteria (from the requirements)
Simplicity · maintainability · community adoption · self-hosting friendliness · ease of backup/recovery · low operational complexity · small image (< 200 MB) and low memory (< 256 MB idle) · exact decimal arithmetic · API-first (OpenAPI) · reproducible build · AGPL-compatible dependencies.

## Constraints already fixed
- One application container, one data volume `/data` (database, backups, keys, future attachments); logs to stdout; configuration by environment variables; non-root with configurable UID/GID; linux/amd64 in V1.
- API-first: the web app consumes the same documented API as future clients.
- Derived data (positions, cash, valuation) is reproducible from transactions and market data.
- Pluggable market data providers and importers behind interfaces.
- Whole-instance backup as a single archive with manifest and format version.
- Secrets: provider credentials encrypted at rest, key from env var or generated into the volume.

## ADRs
| ADR | Topic | Decision in short |
|---|---|---|
| [0001](adr-0001-backend-language-framework.md) | Backend language/framework | Python 3.12, FastAPI, Uvicorn, Pydantic; uv, ruff, pytest |
| [0002](adr-0002-database-and-migrations.md) | Database and migrations | SQLite (WAL) in `/data/db`, SQLAlchemy 2, Alembic, pre-migration copy |
| [0003](adr-0003-frontend-stack-and-charting.md) | Frontend stack and charting | React + TypeScript + Vite, i18next, CSS-variable themes, ECharts (from M5) |
| [0004](adr-0004-api-style.md) | API style and documentation | REST/JSON, generated OpenAPI, `/api/v1`, decimals as strings |
| [0005](adr-0005-authentication-and-sessions.md) | Authentication/session implementation | Opaque hashed sessions, Argon2id, TOTP, trusted-proxy rate limiting |
| [0006](adr-0006-background-jobs.md) | Background jobs / scheduler | APScheduler in-process, single worker |
| [0007](adr-0007-market-data-adapters.md) | Market data adapters | Provider interfaces, Frankfurter FX, failure isolation |
| [0008](adr-0008-backup-archive-format.md) | Backup archive format | ZIP + manifest + SQLite copy, optional encryption |
| [0009](adr-0009-build-ci-release.md) | Build, CI/CD and release | Lockfiles, multi-stage amd64 image, GitHub Actions, license allowlist |
| [0010](adr-0010-repository-layout-and-tests.md) | Repository layout and test strategy | Monorepo `backend/` + `frontend/`; pytest, Vitest |
| [0011](adr-0011-exact-decimals-with-sqlite.md) | Exact decimals with SQLite | Decimals as TEXT via a dedicated column type; Python `Decimal`, half-even 8 dp |

## ADR template
```
# ADR-NNNN: Title
Status: proposed | accepted | superseded by ADR-XXXX
Context: what forces the decision (link requirements/decisions)
Options considered: with pros/cons against the criteria
Decision: what and why
Consequences: what becomes easier/harder
```
