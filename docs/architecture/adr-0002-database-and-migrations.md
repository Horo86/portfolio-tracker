# ADR-0002: Database and migrations

Status: accepted (M0, October 2026)

## Context
One container, one data volume (`/data`), whole-instance backup, automatic migrations with a database copy beforehand (`../requirements.md` § Deployment, § Upgrade Safety; D-22, D-23). Reference size: 10 users × 5,000 transactions, 200 assets × 20 years of daily prices (~1.5 M price rows) — small for any SQL engine.

## Options considered
- **SQLite (embedded, single file)** — no extra process; the database is a file in `/data`; online backup API; WAL gives concurrent readers with one writer, enough for a household instance.
- **PostgreSQL** — rejected by the maintainer: needs a second container (or an extra process in the image), contradicting the single-container model and complicating backup.
- **DuckDB** — analytical engine, weaker for transactional CRUD and migrations tooling.

## Decision
- **SQLite** in **WAL** mode, file `/data/db/app.sqlite` (directory created at startup). Every connection sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.
- **SQLAlchemy 2.1.4** (2.x API: typed `Mapped[...]` models, `select()`), **Alembic 1.20.0** for migrations; the migration scripts live inside the package (`backend/src/portfolio_tracker/migrations/`) so that they ship with the installed application; `backend/alembic.ini` points there for command-line use.
- Migrations run **automatically at startup** (`upgrade head`) before the app accepts requests.
- **Pre-migration copy:** if the database file exists and Alembic reports pending revisions, the app first writes a consistent copy with the SQLite online backup API to `/data/backups/pre-migration/app-<from-revision>-<UTC timestamp>.sqlite`. A new (empty) installation and a start with no pending revision take no copy. If the copy fails, the migration does not run and the app does not start.
- **Foreign keys during migrations (M4):** migrations run with `PRAGMA foreign_keys=OFF` (set before the transaction begins, since SQLite ignores it inside one) and `PRAGMA foreign_key_check` must be empty before commit, otherwise the migration is rolled back and the app does not start. Batch mode rebuilds a table by dropping the original: with foreign keys on, dropping `assets` would have deleted every manual price (`ON DELETE CASCADE`) or failed on transactions. Found by the data-preserving upgrade test of migration `0004`.
- Money and quantities: see ADR-0011.

## Consequences
- Easier: zero database administration; restore = new container + same volume; tests use a temporary file.
- Harder: one writer at a time (fine for the target); schema changes that SQLite cannot `ALTER` use Alembic batch mode; scaling beyond a household instance would need a new ADR.
- Old pre-migration copies are kept; a retention rule is defined with backups in M7.
