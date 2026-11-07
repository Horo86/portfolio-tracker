"""The backup archive (D-169, ADR-0008; format in `docs/backup-format.md`).

`python -m portfolio_tracker backup` writes one ZIP: `manifest.json`, `db/app.sqlite` and the
reserved `attachments/` folder. It reads the live database through the SQLite online backup
API, so it runs while the server runs, and never migrates or changes it. On the copy only, the
provider credentials (D-124) and the sessions are emptied, then the copy is vacuumed, so no free
page keeps their bytes (R-21). The password hashes stay: a restored instance logs in by password.
The `secrets` folder is never read. The archive is not encrypted (encryption comes in a 1.0.x
release): it is written with mode 0600, and the command says so.
"""

import hashlib
import json
import logging
import os
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from portfolio_tracker import __version__
from portfolio_tracker.credentials import KEY_FILE
from portfolio_tracker.migrations_runner import (
    check_free_space,
    copy_database,
    refuse_newer,
)

FORMAT = 1
DB_ENTRY = "db/app.sqlite"
MANIFEST_ENTRY = "manifest.json"
ATTACHMENTS_ENTRY = "attachments/"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupExclusions:
    tables: tuple[str, ...]  # emptied in the database copy
    paths: tuple[Path, ...]  # files or whole folders, relative to the data directory


# Access material, never in an archive: the provider credentials (reusable secrets, D-124) and
# the sessions (token hashes and user agents: ephemeral, a restore logs in again). The whole
# secrets folder: the key file and any temporary copy of it (review L5).
BACKUP_EXCLUDED = BackupExclusions(
    tables=("provider_credentials", "sessions"), paths=(KEY_FILE.parent,)
)


class BackupError(RuntimeError):
    """The backup stops, with a message for the command line; nothing is left behind."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    size: int
    schema_revision: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _scrub(copy: Path) -> str:
    """Empty the excluded tables of the copy, vacuum it, check it; returns its revision."""
    with closing(sqlite3.connect(copy)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        for table in BACKUP_EXCLUDED.tables:
            if table in tables:
                connection.execute(f'DELETE FROM "{table}"')  # names from the constant above
        connection.commit()
        connection.execute("VACUUM")
        check = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        broken = connection.execute("PRAGMA foreign_key_check").fetchall()
        if check != ["ok"] or broken:
            raise BackupError(
                "the database copy failed its integrity check: no archive was written; "
                "the live database was not changed"
            )
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    return "-".join(sorted(row[0] for row in rows)) or "base"


def _fsync_folder(folder: Path) -> None:
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some network shares cannot sync a folder
    finally:
        os.close(fd)


def _verify(archive: Path, manifest: dict) -> None:
    """The archive opens, its entries are exactly the expected ones and match the manifest."""
    with zipfile.ZipFile(archive) as zf:
        if sorted(zf.namelist()) != sorted([MANIFEST_ENTRY, DB_ENTRY, ATTACHMENTS_ENTRY]):
            raise BackupError("the written archive does not have the expected entries")
        if zf.testzip() is not None:
            raise BackupError("the written archive is damaged")
        if json.loads(zf.read(MANIFEST_ENTRY)) != manifest:
            raise BackupError("the written archive's manifest does not read back")
        for item in manifest["files"]:
            with zf.open(item["path"]) as handle:
                digest = hashlib.sha256()
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != item["sha256"]:
                raise BackupError(f"the archive's {item['path']} does not match its checksum")


def make_backup(db_path: Path, output_dir: Path) -> BackupResult:
    """Write a verified archive of `db_path` into `output_dir`. Raises `BackupError` or a
    `MigrationRefused` (a newer database, too little space) before anything is written."""
    if not (db_path.exists() and db_path.stat().st_size > 0):
        raise BackupError(f"there is no database at {db_path}: nothing to back up")
    refuse_newer(db_path)
    check_free_space(db_path, output_dir, for_backup=True)

    created = not output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)
    if created:
        os.chmod(output_dir, 0o700)
    at = datetime.now(UTC)
    name = f"portfolio-{__version__}-{at.strftime('%Y%m%dT%H%M%SZ')}"
    work = output_dir / f".{name}.sqlite.partial"
    partial = output_dir / f"{name}.zip.partial"
    final = output_dir / f"{name}.zip"
    if final.exists():
        raise BackupError(f"{final} exists already: wait a second and run the backup again")
    try:
        # Created empty and private first, so the copy is never readable by others.
        os.close(os.open(work, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        copy_database(db_path, work)
        revision = _scrub(work)
        manifest = {
            "format": FORMAT,
            "app": "portfolio-tracker",
            "app_version": __version__,
            "schema_revision": revision,
            "created_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "encrypted": False,
            "files": [{"path": DB_ENTRY, "size": work.stat().st_size, "sha256": _sha256(work)}],
            "excluded": {
                "tables": list(BACKUP_EXCLUDED.tables),
                "paths": [f"{path.as_posix()}/" for path in BACKUP_EXCLUDED.paths],
            },
        }
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(MANIFEST_ENTRY, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
                zf.write(work, DB_ENTRY)
                zf.writestr(zipfile.ZipInfo(ATTACHMENTS_ENTRY), b"")
            handle.flush()
            os.fsync(handle.fileno())
        _verify(partial, manifest)
        os.replace(partial, final)
        _fsync_folder(output_dir)
    finally:
        work.unlink(missing_ok=True)
        work.with_name(work.name + "-journal").unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    size = final.stat().st_size
    log.info("backup written", extra={"path": str(final), "revision": revision})
    return BackupResult(final, size, revision)
