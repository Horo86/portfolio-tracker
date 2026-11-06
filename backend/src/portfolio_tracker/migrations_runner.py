"""Automatic migrations at startup, with a database copy before any pending migration."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

from portfolio_tracker import __version__
from portfolio_tracker.db import create_db_engine, sqlite_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

log = logging.getLogger(__name__)


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    return config


def copy_database(db_path: Path, target: Path) -> None:
    """Consistent copy with the SQLite online backup API (safe with WAL)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)


class NewerDatabaseError(RuntimeError):
    """The database carries a revision this version does not know: a newer version wrote it
    (an image rolled back, R-18). Raised before any copy or change."""


def _current_revisions(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(MigrationContext.configure(connection).get_current_heads())


def _refuse_unknown(script: ScriptDirectory, current: set[str]) -> None:
    known = {revision.revision for revision in script.walk_revisions()}
    unknown = sorted(current - known)
    if unknown:
        newest = "-".join(sorted(script.get_heads()))
        raise NewerDatabaseError(
            f"the database is at revision {', '.join(unknown)}, which this version "
            f"({__version__}) does not know; the newest it knows is {newest}. A newer version "
            "wrote this database: start the newer image again, or put back the copy of the "
            "data folder made before the upgrade. The database was not changed."
        )


def check_database_known(db_path: Path) -> None:
    """Refuse a database written by a newer version (R-18), before the server starts, so the
    command line shows the message without a traceback. A missing or empty file passes; the
    start then creates it. `run_migrations` makes the same check again."""
    if not (db_path.exists() and db_path.stat().st_size > 0):
        return
    engine = create_db_engine(db_path)
    try:
        current = _current_revisions(engine)
    finally:
        engine.dispose()
    _refuse_unknown(ScriptDirectory.from_config(alembic_config(db_path)), current)


class MigrationIntegrityError(RuntimeError):
    """A migration left rows whose foreign keys point nowhere; it was rolled back."""


@contextmanager
def without_foreign_keys(connection: Connection) -> Iterator[None]:
    """One transaction with foreign keys off, all of them checked before it commits.

    Alembic's batch mode rebuilds a table by copying it and dropping the original. With
    foreign keys on, dropping it would delete the rows of child tables with `ON DELETE
    CASCADE` (e.g. dropping `assets` empties `prices`) or fail on `NO ACTION` references (e.g.
    `transactions`). SQLite ignores the pragma inside a transaction, so it is set on the
    driver connection before BEGIN, and restored afterwards whatever happens. Used by the
    app's upgrade and by the `alembic` command line (`migrations/env.py`), upgrade or
    downgrade alike.
    """
    driver = connection.connection.driver_connection
    driver.execute("PRAGMA foreign_keys=OFF")
    try:
        with connection.begin():
            yield
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise MigrationIntegrityError(
                    f"migration left {len(broken)} broken foreign key reference(s)"
                )
    finally:
        driver.execute("PRAGMA foreign_keys=ON")


def upgrade_without_foreign_keys(engine: Engine, config: Config) -> None:
    """Upgrade to head with foreign keys off, then check them all before committing."""
    with engine.connect() as connection, without_foreign_keys(connection):
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def run_migrations(engine: Engine, db_path: Path, backup_dir: Path) -> Path | None:
    """Upgrade to head. Returns the path of the pre-migration copy, if one was taken. A
    database from a newer version raises `NewerDatabaseError` before any copy (R-18)."""
    existed = db_path.exists() and db_path.stat().st_size > 0
    config = alembic_config(db_path)
    script = ScriptDirectory.from_config(config)
    heads = set(script.get_heads())

    current = _current_revisions(engine)
    _refuse_unknown(script, current)

    if current == heads:
        log.info("database schema up to date", extra={"revision": sorted(current)})
        return None

    copy_path = None
    if existed:
        from_revision = "-".join(sorted(current)) or "base"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        copy_path = backup_dir / f"app-{from_revision}-{stamp}.sqlite"
        copy_database(db_path, copy_path)
        log.info("pre-migration copy written", extra={"path": str(copy_path)})

    upgrade_without_foreign_keys(engine, config)
    log.info("database migrated", extra={"from": sorted(current), "to": sorted(heads)})
    return copy_path
