# ADR-0002: Database and migrations

Status: accepted (M0, October 2026)

## Context
One container, one data volume (`/data`), whole-instance backup, automatic migrations with a database copy beforehand (`../requirements.md` § Deployment, § Upgrade Safety; D-22, D-23). Reference size: 10 users × 5,000 transactions, 200 assets × 20 years of daily prices (~1.0 M price rows on weekdays, ~1.5 M if every calendar day had one; D-154) — small for any SQL engine.

## Options considered
- **SQLite (embedded, single file)** — no extra process; the database is a file in `/data`; online backup API; WAL gives concurrent readers with one writer, enough for a household instance.
- **PostgreSQL** — rejected by the maintainer: needs a second container (or an extra process in the image), contradicting the single-container model and complicating backup.
- **DuckDB** — analytical engine, weaker for transactional CRUD and migrations tooling.

## Decision
- **SQLite** in **WAL** mode, file `/data/db/app.sqlite` (directory created at startup). Every connection sets `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`.
- **SQLAlchemy 2.1.4** (2.x API: typed `Mapped[...]` models, `select()`), **Alembic 1.20.0** for migrations; the migration scripts live inside the package (`backend/src/portfolio_tracker/migrations/`) so that they ship with the installed application; `backend/alembic.ini` points there for command-line use.
- Migrations run **automatically at startup** (`upgrade head`) before the app accepts requests.
- **Pre-migration copy:** if the database file exists and Alembic reports pending revisions, the app first writes a consistent copy with the SQLite online backup API to `/data/backups/pre-migration/app-<from-revision>-<UTC timestamp>.sqlite`. A new (empty) installation and a start with no pending revision take no copy. If the copy fails, the migration does not run and the app does not start. **The upgrade policy (M7, D-168, `migrations_runner`):** the free space is checked first (twice the database and its log plus 64 MiB, `check_free_space`, also run by the command line before the server starts); the copy is written as `.partial` in a single file (rollback journal, no `-wal`), synced, opened read-only and checked (`quick_check`, its revision), renamed, and the folder synced (R-20); a record file `app-<from>-<stamp>.json` beside it gives the revisions, the version, the time, the size and the outcome. A fresh database and one at the head take no copy and no record. A failed migration is rolled back (one transaction) and leaves `db/migration-failed.json`: the same version then refuses at once, with no new copy, and says how to retry (read the error, delete that file, start again); another version clears it and tries again. After a successful migration the three newest copies are kept (R-22); a failed run deletes nothing. Data migrations that need the network only mark rows for the background loop; forward-only for users.
- **A newer database is refused (M7, R-18):** if the database carries a revision this version does not know (it was written by a newer version, e.g. after an image rollback), the start and both container commands stop with a message naming that revision and the newest one known, before any copy and without changing the file; the remedy is the newer image again or the copy of the data folder made before the upgrade (`migrations_runner.NewerDatabaseError`).
- **Foreign keys during migrations (M4):** migrations run with `PRAGMA foreign_keys=OFF` (set before the transaction begins, since SQLite ignores it inside one) and `PRAGMA foreign_key_check` must be empty before commit, otherwise the migration is rolled back and the app does not start. Batch mode rebuilds a table by dropping the original: with foreign keys on, dropping `assets` would have deleted every manual price (`ON DELETE CASCADE`) or failed on transactions. Found by the data-preserving upgrade test of migration `0004`. The same applies to the `alembic` command line (`upgrade`, `downgrade`, through `migrations/env.py`), which first did not turn them off and silently deleted every manual price on `0003 → 0004` (final M4 review, M-3); both paths now share `migrations_runner.without_foreign_keys`.
- Money and quantities: see ADR-0011.

## Consequences
- Easier: zero database administration; restore = new container + same volume; tests use a temporary file.
- Harder: one writer at a time (fine for the target); schema changes that SQLite cannot `ALTER` use Alembic batch mode; scaling beyond a household instance would need a new ADR.
- Retention of the pre-migration copies: the three newest are kept (D-168).
