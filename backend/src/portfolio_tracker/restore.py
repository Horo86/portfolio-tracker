"""Restore a backup archive into an instance without users (D-169 (2), D-34, R-19).

`python -m portfolio_tracker restore <archive>` runs with the server stopped: it takes the data
folder's lock (`server_lock`), so it refuses while the app runs. Everything is checked before
anything is written: the target has no database or one without users; the archive is a ZIP
with exactly the entries of `backup.py`, a manifest of format 1, not encrypted, and a schema
revision this version knows (not one of a preview version, D-173); the free space suffices
for the declared size. Only
`db/app.sqlite` is read from the ZIP (no path of the archive is ever used); it is streamed to
`db/app.sqlite.partial`, refused as soon as it grows past the declared size, compared with the
manifest's size and SHA-256, checked (`integrity_check`, its revision), then renamed. The file
gets the mode the app gives a database it creates (0644 less the umask). The next start
migrates it forward with the usual pre-migration copy (D-168). The `secrets` folder is never
touched: the archive holds no provider credential.
"""

import hashlib
import json
import os
import re
import sqlite3
import zipfile
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from alembic.script import ScriptDirectory

from portfolio_tracker import __version__, server_lock
from portfolio_tracker.backup import ATTACHMENTS_ENTRY, DB_ENTRY, FORMAT, MANIFEST_ENTRY
from portfolio_tracker.migrations_runner import (
    FAILED_MARK,
    PREVIEW_REVISIONS,
    SPACE_MARGIN,
    alembic_config,
    free_bytes,
)

DB_FILE_MODE = 0o644  # SQLite's own default for a new database file, less the umask
MAX_MANIFEST_BYTES = 64 * 1024
CHUNK = 1 << 20
_SHA256 = re.compile(r"[0-9a-f]{64}")


class RestoreError(RuntimeError):
    """The restore stops, with a message for the command line; the target is unchanged."""


@dataclass(frozen=True)
class RestoreResult:
    db_path: Path
    schema_revision: str
    app_version: str
    created_at: str


def _refuse_users(db_path: Path) -> None:
    """Refuse a database with a user. It is read with its log (a `-wal` left by a crash may
    hold users), and the `-wal`/`-shm` files the read itself creates are removed again, so a
    refused instance is left exactly as it was."""
    if not (db_path.exists() and db_path.stat().st_size > 0):
        return
    sidecars = [db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm")]
    existed = {path for path in sidecars if path.exists()}
    try:
        with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
            has_users = "users" in tables and db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    except sqlite3.Error:
        raise RestoreError(
            f"the database {db_path} cannot be read: move the db folder aside (keep it) and run "
            "the restore again"
        ) from None
    finally:
        for path in sidecars:
            if path not in existed:
                path.unlink(missing_ok=True)
    if has_users:
        raise RestoreError(
            f"this instance already has users ({db_path}): a restore goes only into an instance "
            "without users. Use a new data folder, or stop the app and move its db folder aside "
            "(keep it), then run the restore again"
        )


def _manifest(zf: zipfile.ZipFile) -> dict:
    names = sorted(zf.namelist())
    if names != sorted([MANIFEST_ENTRY, DB_ENTRY, ATTACHMENTS_ENTRY]):
        raise RestoreError(f"not a backup archive of this app: unexpected entries {names}")
    if zf.getinfo(MANIFEST_ENTRY).file_size > MAX_MANIFEST_BYTES:
        raise RestoreError("the archive's manifest is too large: not a backup archive of this app")
    try:
        manifest = json.loads(zf.read(MANIFEST_ENTRY))
    except (ValueError, zipfile.BadZipFile, zlib.error):
        raise RestoreError("the archive's manifest cannot be read") from None
    if not isinstance(manifest, dict) or manifest.get("app") != "portfolio-tracker":
        raise RestoreError("not a backup archive of this app")
    if manifest.get("format") != FORMAT:
        raise RestoreError(
            f"the archive has format {manifest.get('format')!r}, this version reads format "
            f"{FORMAT}: restore it with a newer version"
        )
    if manifest.get("encrypted") is not False:
        raise RestoreError("the archive is encrypted: this version cannot restore it")
    files = manifest.get("files")
    if not (isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)):
        raise RestoreError("the archive's manifest does not list its database")
    item = files[0]
    size, digest = item.get("size"), item.get("sha256")
    if (
        item.get("path") != DB_ENTRY
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or not (isinstance(digest, str) and _SHA256.fullmatch(digest))
    ):
        raise RestoreError("the archive's manifest does not describe its database")
    if not isinstance(manifest.get("schema_revision"), str):
        raise RestoreError("the archive's manifest has no schema revision")
    return manifest


def _refuse_unknown_revision(db_path: Path, revision: str) -> None:
    preview = sorted(set(revision.split("-")) & PREVIEW_REVISIONS)
    if preview:  # D-173: a preview's data is never upgraded, so it is not restored either
        raise RestoreError(
            f"the archive comes from a preview version (0.9.0 or 1.0.0-rc.1: database revision "
            f"{', '.join(preview)}), which version {__version__} does not upgrade; start from an "
            "empty folder and enter the data again, or keep using the preview"
        )
    script = ScriptDirectory.from_config(alembic_config(db_path))
    known = {item.revision for item in script.walk_revisions()}
    if any(part not in known for part in revision.split("-")):
        newest = "-".join(sorted(script.get_heads()))
        raise RestoreError(
            f"the archive's database is at revision {revision}, which this version does not "
            f"know (the newest it knows is {newest}): a newer version wrote it; restore it with "
            "that version or a later one"
        )


def _check_space(folder: Path, size: int) -> None:
    needed = 2 * size + SPACE_MARGIN  # the database, then the copy before its first upgrade
    available = free_bytes(folder)
    if available < needed:
        mib = 1024 * 1024
        raise RestoreError(
            f"not enough free space for the restore: about {needed // mib + 1} MiB are needed, "
            f"{available // mib} MiB are free under {folder}"
        )


def _stream(zf: zipfile.ZipFile, target: Path, size: int, digest: str) -> None:
    """Write the database entry to `target`, never past the declared size."""
    hasher, written = hashlib.sha256(), 0
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, DB_FILE_MODE)
    with os.fdopen(fd, "wb") as out:
        try:
            with zf.open(DB_ENTRY) as source:
                while block := source.read(CHUNK):
                    written += len(block)
                    if written > size:
                        raise RestoreError(
                            f"the archive's database is larger than its manifest says ({size} "
                            "bytes): the archive is damaged or not genuine"
                        )
                    hasher.update(block)
                    out.write(block)
        except (zipfile.BadZipFile, zlib.error, EOFError):
            raise RestoreError("the archive is damaged: its database cannot be read") from None
        out.flush()
        os.fsync(out.fileno())
    if written != size:
        raise RestoreError(
            f"the archive's database has {written} bytes, its manifest says {size}: the archive "
            "is damaged"
        )
    if hasher.hexdigest() != digest:
        raise RestoreError("the archive's database does not match its checksum: it is damaged")


def _check_database(path: Path, revision: str) -> None:
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
            check = [row[0] for row in db.execute("PRAGMA integrity_check")]
            rows = db.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error:
        raise RestoreError("the archive's database cannot be opened: it is damaged") from None
    if check != ["ok"]:
        raise RestoreError("the archive's database failed its integrity check")
    found = "-".join(sorted(row[0] for row in rows))
    if found != revision:
        raise RestoreError(
            f"the archive's database is at revision {found}, its manifest says {revision}"
        )


def _fsync_folder(folder: Path) -> None:
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def restore(archive: Path, data_dir: Path) -> RestoreResult:
    db_path = data_dir / "db" / "app.sqlite"
    if not archive.is_file():
        raise RestoreError(f"there is no archive at {archive}")
    try:
        lock = server_lock.acquire(data_dir, purpose="restore")
    except server_lock.DataFolderBusy as error:
        raise RestoreError(str(error)) from None
    partial = db_path.with_name(db_path.name + ".partial")
    try:
        _refuse_users(db_path)
        try:
            zf = zipfile.ZipFile(archive)
        except (zipfile.BadZipFile, OSError):
            raise RestoreError(
                f"{archive} is not a backup archive (not a ZIP file, or damaged)"
            ) from None
        with zf:
            manifest = _manifest(zf)
            revision = manifest["schema_revision"]
            item = manifest["files"][0]
            _refuse_unknown_revision(db_path, revision)
            _check_space(db_path.parent, item["size"])
            partial.unlink(missing_ok=True)  # left by an interrupted restore
            _stream(zf, partial, item["size"], item["sha256"])
        _check_database(partial, revision)
        for stale in ("-wal", "-shm", "-journal"):
            db_path.with_name(db_path.name + stale).unlink(missing_ok=True)
        (db_path.parent / FAILED_MARK).unlink(missing_ok=True)  # it was about the old database
        os.replace(partial, db_path)
        _fsync_folder(db_path.parent)
    finally:
        partial.unlink(missing_ok=True)
        server_lock.release(lock)
    return RestoreResult(
        db_path, revision, str(manifest.get("app_version", "?")), str(manifest.get("created_at"))
    )
