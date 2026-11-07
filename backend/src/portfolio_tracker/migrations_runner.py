"""Automatic migrations at startup, with a verified database copy before any pending migration.

The upgrade policy (D-168, ADR-0002): migrations run only when the database is not at the head;
a database from a newer version is refused (R-18); before a migration the free space is
checked, then a copy is written as `.partial`, checked and renamed (R-20), with a record file
next to it; all pending revisions run in one transaction, so a failure leaves the database at
its old revision and a mark that stops the same version from trying again in a loop; after a
success the three newest copies are kept (R-22). A fresh database and one already at the head
take no copy and no record.
"""

import json
import logging
import os
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine

from portfolio_tracker import __version__
from portfolio_tracker.db import create_db_engine, sqlite_url

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
FAILED_MARK = "migration-failed.json"  # next to the database
SPACE_MARGIN = 64 * 1024 * 1024  # bytes, on top of twice the database size
KEEP_COPIES = 3  # pre-migration copies kept after a successful migration (R-22)

# app-<from revision>-<UTC stamp>.sqlite; the stamp of 0.9.0 has no fraction of a second.
_COPY_NAME = re.compile(r"app-(?P<revision>.+)-(?P<stamp>\d{8}T\d{6})(?:\.(?P<fraction>\d{6}))?Z")

log = logging.getLogger(__name__)


def alembic_config(db_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    return config


def copy_database(db_path: Path, target: Path) -> None:
    """Consistent copy with the SQLite online backup API (safe with WAL). The copy is set back
    to a rollback journal, so it is one file with no `-wal` or `-shm` beside it, and a
    read-only check leaves none behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
        dest.execute("PRAGMA journal_mode=DELETE")


class MigrationRefused(RuntimeError):
    """The start stops before any change, with a message for the command line."""


class NewerDatabaseError(MigrationRefused):
    """The database carries a revision this version does not know: a newer version wrote it
    (an image rolled back, R-18). Raised before any copy or change."""


class NotEnoughSpaceError(MigrationRefused):
    """Too little free space for the pre-migration copy and the migration (D-168)."""


class PreviousMigrationFailedError(MigrationRefused):
    """This version already failed to migrate this database; it does not try again by itself,
    so a restart policy cannot fill the disk with copies (D-168)."""


class CopyCheckError(MigrationRefused):
    """The pre-migration copy did not pass its check; nothing was migrated (R-20)."""


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


def _revision_text(revisions: set[str]) -> str:
    return "-".join(sorted(revisions)) or "base"


def _has_data(db_path: Path) -> bool:
    return db_path.exists() and db_path.stat().st_size > 0


def failed_mark_path(db_path: Path) -> Path:
    return db_path.parent / FAILED_MARK


def database_size(db_path: Path) -> int:
    """The database file and its write-ahead log, in bytes."""
    wal = db_path.with_name(db_path.name + "-wal")
    return sum(path.stat().st_size for path in (db_path, wal) if path.exists())


def free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def check_free_space(db_path: Path, backup_dir: Path, *, for_backup: bool = False) -> None:
    """Refuse a migration with less free space than twice the database plus a margin: the copy,
    then the tables Alembic rebuilds and the log growth (D-168); for a backup archive, the
    working copy and the archive (D-169). The only space check, used by the runner, by the
    command line before the server starts and by the backup command."""
    needed = 2 * database_size(db_path) + SPACE_MARGIN
    folder = backup_dir
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    available = free_bytes(folder)
    if available < needed:
        mib = 1024 * 1024
        what, uses = (
            ("for the backup", "the working copy and the archive")
            if for_backup
            else ("to upgrade the database", "the copy taken first and the upgrade")
        )
        raise NotEnoughSpaceError(
            f"not enough free space {what}: about {needed // mib + 1} MiB are needed for "
            f"{uses}, {available // mib} MiB are free under {folder}. Free some space and "
            "start again. The database was not changed."
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _fsync_folder(folder: Path) -> None:
    # Some file systems (network shares) cannot sync a folder: the rename stays, unsynced.
    with suppress(OSError):
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Written whole or not at all: a `.partial` file, synced, then renamed."""
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    _fsync_folder(path.parent)


def _check_mark(db_path: Path, *, clear_other_version: bool) -> None:
    """Refuse when this version already failed on this database. A mark left by another
    version is cleared by the runner (a fixed version tries again); the command line's check
    leaves it for the runner."""
    mark = failed_mark_path(db_path)
    if not mark.exists():
        return
    data = _read_json(mark) or {}
    version = data.get("version")
    if version is not None and version != __version__:
        if clear_other_version:
            log.info(
                "a previous upgrade failed with another version: trying again",
                extra={"failed_version": version, "version": __version__},
            )
            mark.unlink(missing_ok=True)
        return
    raise PreviousMigrationFailedError(
        f"the upgrade of the database to revision {data.get('to', '?')} by version "
        f"{version or '?'} failed (error {data.get('error', '?')}); the database was left "
        f"unchanged at revision {data.get('from', '?')}. It is not tried again by itself, so "
        "restarts cannot fill the disk with copies. To try again (for example after a "
        f"temporary problem), read the error in the log, then delete {mark} and start again; "
        "or start the previous version's image."
    )


def refuse_newer(db_path: Path) -> None:
    """R-18 alone, for a command that reads the database without migrating it (the backup)."""
    engine = create_db_engine(db_path)
    try:
        current = _current_revisions(engine)
    finally:
        engine.dispose()
    _refuse_unknown(ScriptDirectory.from_config(alembic_config(db_path)), current)


def preflight(db_path: Path, backup_dir: Path) -> None:
    """The refusals of the runner, before the server starts, so the command line shows the
    message without a traceback: a newer database (R-18), a migration this version already
    failed, too little free space. A missing or empty file and a database at the head pass
    without further checks; `run_migrations` makes the same checks again."""
    if not _has_data(db_path):
        return
    engine = create_db_engine(db_path)
    try:
        current = _current_revisions(engine)
    finally:
        engine.dispose()
    script = ScriptDirectory.from_config(alembic_config(db_path))
    _refuse_unknown(script, current)
    if current == set(script.get_heads()):
        return
    _check_mark(db_path, clear_other_version=False)
    check_free_space(db_path, backup_dir)


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


def _verify_copy(path: Path, from_revision: str) -> None:
    """The copy opens read-only, passes `quick_check` and carries the revision it copied."""
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            check = [row[0] for row in connection.execute("PRAGMA quick_check")]
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
            revisions: set[str] = set()
            if "alembic_version" in tables:
                rows = connection.execute("SELECT version_num FROM alembic_version")
                revisions = {row[0] for row in rows}
    except sqlite3.Error as error:
        raise CopyCheckError(
            f"the copy taken before the upgrade could not be read ({type(error).__name__}); "
            "nothing was upgraded and the database was not changed"
        ) from None
    if check != ["ok"]:
        raise CopyCheckError(
            "the copy taken before the upgrade failed its integrity check; nothing was "
            "upgraded and the database was not changed"
        )
    if _revision_text(revisions) != from_revision:
        raise CopyCheckError(
            f"the copy taken before the upgrade is at revision {_revision_text(revisions)}, "
            f"not {from_revision}; nothing was upgraded and the database was not changed"
        )


def _take_copy(
    db_path: Path, backup_dir: Path, from_revision: str, to_revision: str
) -> tuple[Path, Path, dict[str, Any]]:
    """The verified copy (R-20) and its record file, status `pending`."""
    at = datetime.now(UTC)
    name = f"app-{from_revision}-{at.strftime('%Y%m%dT%H%M%S.%fZ')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    partial = backup_dir / f"{name}.sqlite.partial"
    copy_path = backup_dir / f"{name}.sqlite"
    try:
        copy_database(db_path, partial)
        _fsync_file(partial)
        _verify_copy(partial, from_revision)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, copy_path)
    _fsync_folder(backup_dir)
    record_path = backup_dir / f"{name}.json"
    record = {
        "from": from_revision,
        "to": to_revision,
        "version": __version__,
        "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "size": copy_path.stat().st_size,
        "copy": copy_path.name,
        "status": "pending",
    }
    _write_json(record_path, record)
    log.info("pre-migration copy written", extra={"path": str(copy_path)})
    return copy_path, record_path, record


def _copy_order(path: Path) -> tuple[str, str] | None:
    match = _COPY_NAME.fullmatch(path.name.removesuffix(".sqlite"))
    if match is None:
        return None
    return match["stamp"], match["fraction"] or "000000"


def _retain(backup_dir: Path, newest: Path) -> None:
    """Keep the newest copies (R-22): the one just taken is never deleted; older copies, their
    record files and any `.partial` file left by an interrupted start are removed. Files whose
    names this version did not write are left alone."""
    copies = sorted(
        (order, path)
        for path in backup_dir.glob("app-*.sqlite")
        if path != newest and (order := _copy_order(path)) is not None
    )
    kept = [path for _, path in copies][-(KEEP_COPIES - 1) :] if KEEP_COPIES > 1 else []
    for _, path in copies:
        if path in kept:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(".json").unlink(missing_ok=True)
        log.info("old pre-migration copy deleted", extra={"path": str(path)})
    for partial in backup_dir.glob("app-*.partial"):
        partial.unlink(missing_ok=True)


def run_migrations(engine: Engine, db_path: Path, backup_dir: Path) -> Path | None:
    """Upgrade to head. Returns the path of the pre-migration copy, if one was taken. Raises a
    `MigrationRefused` before any change (newer database, a failure of this version already
    marked, too little space, a copy that fails its check); a failed migration leaves the
    database at its old revision, marks it and raises."""
    existed = _has_data(db_path)
    config = alembic_config(db_path)
    script = ScriptDirectory.from_config(config)
    heads = set(script.get_heads())

    current = _current_revisions(engine)
    _refuse_unknown(script, current)

    mark = failed_mark_path(db_path)
    if current == heads:
        if mark.exists():  # migrated since (another version, or by hand): the mark is stale
            mark.unlink(missing_ok=True)
        log.info("database schema up to date", extra={"revision": sorted(current)})
        return None

    from_revision, to_revision = _revision_text(current), _revision_text(heads)
    _check_mark(db_path, clear_other_version=True)

    copy_path = record_path = record = None
    if existed:
        check_free_space(db_path, backup_dir)
        copy_path, record_path, record = _take_copy(db_path, backup_dir, from_revision, to_revision)

    try:
        upgrade_without_foreign_keys(engine, config)
    except Exception as error:
        log.error(
            "database migration failed: the database was left at its revision",
            extra={"from": from_revision, "to": to_revision, "type": type(error).__name__},
        )
        mark.parent.mkdir(parents=True, exist_ok=True)
        _write_json(
            mark,
            {
                "version": __version__,
                "from": from_revision,
                "to": to_revision,
                "error": type(error).__name__,
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "copy": copy_path.name if copy_path else None,
            },
        )
        if record_path is not None and record is not None:
            _write_json(record_path, {**record, "status": "failed"})
        raise

    if record_path is not None and record is not None and copy_path is not None:
        _write_json(record_path, {**record, "status": "done"})
        _retain(backup_dir, copy_path)
    log.info("database migrated", extra={"from": sorted(current), "to": sorted(heads)})
    return copy_path
