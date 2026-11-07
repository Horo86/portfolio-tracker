"""The restore command and the data folder's lock (D-169 (2), R-19, D-34; `docs/backup-format.md`).

The archives come from the backup command run on a live instance (the newest upgrade fixture:
invented data, `ZZ` ISINs, a provider credential under the fake test key, open sessions).
Damaged and forged archives are built from a genuine one. Every refusal leaves the target as it
was and no `.partial` file.
"""

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from test_upgrade_paths import (
    CSRF,
    INVESTOR,
    assert_contains,
    fixture_app,
    fixture_settings,
    snapshot,
)

from portfolio_tracker import migrations_runner, server_lock
from portfolio_tracker import restore as restore_module
from portfolio_tracker.__main__ import main
from portfolio_tracker.backup import make_backup
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import FAILED_MARK, run_migrations
from portfolio_tracker.restore import RestoreError, restore


def rows(db_path: Path, query: str) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(query).fetchall()


def target_state(data_dir: Path) -> dict[str, bytes]:
    """Every file under the target's db folder (the lock file aside), to show nothing changed."""
    folder = data_dir / "db"
    if not folder.exists():
        return {}
    return {
        path.name: path.read_bytes()
        for path in sorted(folder.iterdir())
        if path.is_file() and path.name != ".server.lock"
    }


@pytest.fixture
def archive(live: Settings) -> Path:
    return make_backup(live.db_path, live.manual_backup_dir).path


def forged(archive: Path, out: Path, *, manifest=None, db: bytes | None = None, extra=None) -> Path:
    """A copy of `archive` with its manifest changed (a function of it), its database replaced
    or an extra entry; the manifest's size and checksum are not updated unless said."""
    with zipfile.ZipFile(archive) as source:
        data = json.loads(source.read("manifest.json"))
        database = source.read("db/app.sqlite")
    if manifest is not None:
        data = manifest(data)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(data))
        zf.writestr("db/app.sqlite", database if db is None else db)
        zf.writestr(zipfile.ZipInfo("attachments/"), b"")
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return out


# --- the round trip ---------------------------------------------------------------------------


def test_a_restore_into_a_new_folder_gives_back_the_same_instance(
    live: Settings, archive: Path, tmp_path: Path
) -> None:
    with fixture_app(live) as client:
        before = snapshot(client)
    # A new installation: its own, newly generated key (the archive holds no credential).
    target = Settings(data_dir=tmp_path / "new", market_data=False)

    result = restore(archive, target.data_dir)

    assert result.db_path == target.db_path and result.schema_revision == "0006"
    assert not list(target.db_path.parent.glob("*.partial"))
    with fixture_app(target) as client:
        after = snapshot(client)  # it logs in as the investor, with the password
    assert_contains(before, after)
    assert after["export_csv"] == before["export_csv"]
    assert rows(target.db_path, "SELECT count(*) FROM provider_credentials") == [(0,)]
    # Only the sessions opened by the snapshot's own login, already logged out: none left.
    assert rows(target.db_path, "SELECT count(*) FROM sessions") == [(0,)]
    assert (target.data_dir / "secrets" / "secret.key").exists()  # its own key, untouched


def test_the_restored_database_has_the_mode_of_a_database_the_app_creates(
    archive: Path, tmp_path: Path
) -> None:
    created = tmp_path / "created" / "db" / "app.sqlite"
    engine = create_db_engine(created)
    run_migrations(engine, created, tmp_path / "created" / "backups")
    engine.dispose()

    result = restore(archive, tmp_path / "restored")

    expected = stat.S_IMODE(created.stat().st_mode)
    assert stat.S_IMODE(result.db_path.stat().st_mode) == expected
    old_umask = os.umask(0o077)  # under another umask, the same rule (0644 less the umask)
    try:
        other = restore(archive, tmp_path / "private")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(other.db_path.stat().st_mode) == 0o600


def test_a_restore_goes_into_a_started_instance_without_users(
    archive: Path, tmp_path: Path
) -> None:
    target = Settings(data_dir=tmp_path / "started", market_data=False)
    with AppRun(target):
        pass  # the first start: a database at the head, no user
    db = target.db_path
    db.with_name("app.sqlite-wal").write_bytes(b"stale")
    db.with_name("app.sqlite-shm").write_bytes(b"stale")
    (db.parent / FAILED_MARK).write_text("{}")

    restore(archive, target.data_dir)

    assert not db.with_name("app.sqlite-wal").exists()
    assert not db.with_name("app.sqlite-shm").exists()
    assert not (db.parent / FAILED_MARK).exists()
    assert rows(db, "SELECT username FROM users ORDER BY username") == [("admin",), ("investor",)]


def test_an_older_archive_is_restored_then_upgraded_at_the_next_start(tmp_path: Path) -> None:
    from alembic import command

    source = tmp_path / "old" / "db" / "app.sqlite"
    engine = create_db_engine(source)
    config = migrations_runner.alembic_config(source)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
    engine.dispose()
    old = make_backup(source, tmp_path / "out")
    target = Settings(data_dir=tmp_path / "target", market_data=False)

    assert restore(old.path, target.data_dir).schema_revision == "0005"
    with AppRun(target):
        pass

    assert rows(target.db_path, "SELECT version_num FROM alembic_version") == [("0006",)]
    [copy] = target.pre_migration_backup_dir.glob("app-0005-*.sqlite")
    assert json.loads(copy.with_suffix(".json").read_text())["status"] == "done"


# --- refusals ---------------------------------------------------------------------------------


def refused(archive: Path, data_dir: Path, match: str) -> None:
    before = target_state(data_dir)
    with pytest.raises(RestoreError, match=match):
        restore(archive, data_dir)
    assert target_state(data_dir) == before
    assert not list((data_dir / "db").glob("*.partial")) if (data_dir / "db").exists() else True


def test_an_instance_with_users_is_refused(live: Settings, archive: Path) -> None:
    refused(archive, live.data_dir, "already has users")


def test_users_only_in_a_log_left_by_a_crash_are_seen_and_refused(
    archive: Path, live: Settings, tmp_path: Path
) -> None:
    """A server killed before its checkpoint leaves users in `-wal` only: still an instance
    with users, so the check reads the database with its log."""
    empty = Settings(data_dir=tmp_path / "empty", market_data=False)
    with AppRun(empty):
        pass  # a database at the head, no user, checkpointed: no -wal
    work = tmp_path / "work.sqlite"
    work.write_bytes(empty.db_path.read_bytes())
    writer = sqlite3.connect(work)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("ATTACH DATABASE ? AS live", (str(live.db_path),))
    writer.execute("INSERT INTO main.users SELECT * FROM live.users")
    writer.commit()
    writer.execute("DETACH DATABASE live")
    crashed = tmp_path / "crashed" / "db"
    crashed.mkdir(parents=True)
    # Copied while the writer is still open: the files a crash leaves behind.
    (crashed / "app.sqlite").write_bytes(work.read_bytes())
    (crashed / "app.sqlite-wal").write_bytes(Path(f"{work}-wal").read_bytes())
    writer.close()
    main_file_only = f"{(crashed / 'app.sqlite').as_uri()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(main_file_only, uri=True)) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (0,)

    refused(archive, crashed.parent, "already has users")


def test_a_running_app_is_refused(archive: Path, tmp_path: Path) -> None:
    target = Settings(data_dir=tmp_path / "running", market_data=False)
    with AppRun(target):
        refused(archive, target.data_dir, "another process is using the data folder")


def test_a_database_larger_than_declared_is_refused_as_soon_as_it_grows_past(
    archive: Path, tmp_path: Path, monkeypatch
) -> None:
    with zipfile.ZipFile(archive) as zf:
        database = zf.read("db/app.sqlite")
    bigger = forged(archive, tmp_path / "bigger.zip", db=database + b"\0" * (3 << 20))
    read = []
    real_stream = restore_module._stream

    def counting(zf, target, size, digest):
        real_open = zf.open

        def opening(name, *args, **kwargs):
            handle = real_open(name, *args, **kwargs)
            real_read = handle.read

            def reading(n=-1):
                block = real_read(n)
                read.append(len(block))
                return block

            handle.read = reading
            return handle

        zf.open = opening
        return real_stream(zf, target, size, digest)

    monkeypatch.setattr(restore_module, "_stream", counting)

    refused(bigger, tmp_path / "target", "larger than its manifest says")
    # It stopped at the first block past the declared size, not at the end of the entry.
    assert sum(read) <= len(database) + restore_module.CHUNK
    assert sum(read) < len(database) + (3 << 20)


def test_the_declared_size_is_checked_against_the_free_space_first(
    archive: Path, tmp_path: Path, monkeypatch
) -> None:
    def huge(data):
        data["files"][0]["size"] = 50 * 1024**3  # 50 GiB declared
        return data

    big = forged(archive, tmp_path / "huge.zip", manifest=huge)
    monkeypatch.setattr(migrations_runner, "free_bytes", lambda folder: 1024**3)
    monkeypatch.setattr(restore_module, "free_bytes", lambda folder: 1024**3)
    streamed = []
    monkeypatch.setattr(restore_module, "_stream", lambda *a: streamed.append(a))

    refused(big, tmp_path / "target", "not enough free space for the restore")
    assert streamed == []


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda m: m | {"encrypted": True}, "is encrypted"),
        (lambda m: m | {"format": 2}, "restore it with a newer version"),
        (lambda m: m | {"app": "something-else"}, "not a backup archive of this app"),
        (lambda m: m | {"schema_revision": "9999"}, "which this version does not know"),
        (lambda m: m | {"schema_revision": "0005"}, "its manifest says 0005"),
        (lambda m: {**m, "files": []}, "does not list its database"),
        (lambda m: {**m, "files": [{**m["files"][0], "sha256": "0" * 64}]}, "checksum"),
        (lambda m: {**m, "files": [{**m["files"][0], "size": m["files"][0]["size"] + 1}]},
         "its manifest says"),
        (lambda m: {**m, "files": [{**m["files"][0], "size": "12"}]}, "does not describe"),
    ],
    ids=["encrypted", "format", "app", "newer", "revision", "files", "checksum", "smaller",
         "size-type"],
)  # fmt: skip
def test_a_forged_manifest_is_refused(archive: Path, tmp_path: Path, change, match) -> None:
    refused(forged(archive, tmp_path / "forged.zip", manifest=change), tmp_path / "t", match)


def test_missing_and_extra_entries_are_refused(archive: Path, tmp_path: Path) -> None:
    extra = forged(archive, tmp_path / "extra.zip", extra={"../escape.txt": "x"})
    refused(extra, tmp_path / "t1", "unexpected entries")
    with zipfile.ZipFile(tmp_path / "missing.zip", "w") as zf, zipfile.ZipFile(archive) as source:
        zf.writestr("manifest.json", source.read("manifest.json"))
    refused(tmp_path / "missing.zip", tmp_path / "t2", "unexpected entries")
    assert not (tmp_path / "escape.txt").exists()


def test_a_damaged_or_truncated_archive_is_refused(archive: Path, tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.zip"
    truncated.write_bytes(archive.read_bytes()[: archive.stat().st_size // 2])
    refused(truncated, tmp_path / "t1", "not a backup archive")
    (tmp_path / "text.zip").write_text("not a zip")
    refused(tmp_path / "text.zip", tmp_path / "t2", "not a backup archive")
    with zipfile.ZipFile(archive) as zf:
        database = zf.read("db/app.sqlite")
    garbage = b"x" * len(database)

    def same_size(m):
        m["files"][0]["sha256"] = hashlib.sha256(garbage).hexdigest()
        return m

    refused(forged(archive, tmp_path / "garbage.zip", manifest=same_size, db=garbage),
            tmp_path / "t3", "cannot be opened|integrity")  # fmt: skip
    refused(tmp_path / "nothing.zip", tmp_path / "t4", "there is no archive")


# --- the lock -----------------------------------------------------------------------------------


def test_the_app_holds_the_lock_while_it_runs_and_frees_it_when_it_stops(tmp_path: Path) -> None:
    target = Settings(data_dir=tmp_path / "data", market_data=False)
    with AppRun(target):
        with pytest.raises(server_lock.DataFolderBusy, match="stop it before the restore"):
            server_lock.acquire(target.data_dir, purpose="restore")
        second = AppRun(target)  # a second app on the same folder does not start
        with pytest.raises(server_lock.DataFolderBusy, match="stop it before the start"), second:
            pass
    server_lock.release(server_lock.acquire(target.data_dir, purpose="restore"))


def test_a_file_system_without_locks_warns_and_goes_on(tmp_path: Path, monkeypatch, caplog) -> None:
    def no_locks(fd, operation):
        raise OSError(37, "No locks available")  # ENOLCK

    monkeypatch.setattr(server_lock.fcntl, "flock", no_locks)
    with caplog.at_level("WARNING"):
        assert server_lock.acquire(tmp_path, purpose="restore") is None
    assert "cannot be locked" in caplog.text


# --- the command line -------------------------------------------------------------------------


def test_the_command_restores_and_says_what_to_do_next(
    archive: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "new"))

    assert main(["restore", str(archive)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"Restored: {tmp_path / 'new' / 'db' / 'app.sqlite'} (schema 0006")
    assert "Start the app" in out and "enters the provider" in out

    assert main(["restore", str(archive)]) == 1  # now it has users
    err = capsys.readouterr().err
    assert err.startswith("error: this instance already has users") and "Traceback" not in err


def test_the_restored_instance_logs_in_by_password(archive: Path, tmp_path: Path) -> None:
    target = fixture_settings(tmp_path / "new")
    restore(archive, target.data_dir)
    with fixture_app(target) as client:
        wrong = client.post(
            "/api/v1/auth/login",
            headers=CSRF,
            json={"username": INVESTOR[0], "password": "not the password"},
        )
        right = client.post("/api/v1/auth/login", headers=CSRF,
                            json={"username": INVESTOR[0], "password": INVESTOR[1]})  # fmt: skip
    assert wrong.status_code == 401 and right.status_code == 200


class AppRun:
    """The app's lifespan on `settings` (start and stop), without requests."""

    def __init__(self, settings: Settings) -> None:
        from fastapi.testclient import TestClient

        self.client = TestClient(create_app(settings), base_url="https://localhost")

    def __enter__(self):
        return self.client.__enter__()

    def __exit__(self, *exc):
        return self.client.__exit__(*exc)
