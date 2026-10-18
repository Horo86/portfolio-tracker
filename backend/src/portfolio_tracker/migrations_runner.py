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

from portfolio_tracker.db import sqlite_url

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
    """Upgrade to head. Returns the path of the pre-migration copy, if one was taken."""
    existed = db_path.exists() and db_path.stat().st_size > 0
    config = alembic_config(db_path)
    heads = set(ScriptDirectory.from_config(config).get_heads())

    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())

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
