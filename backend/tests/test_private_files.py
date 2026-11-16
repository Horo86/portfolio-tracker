"""The database, its folder and its copies are for the app's user alone (`private_files`):
folders 0700 and files 0600, whatever the umask, and nothing the app does not own is touched."""

import logging
import os
import shutil
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from test_upgrade_paths import FIXTURES, fixture_app, fixture_settings

from portfolio_tracker import private_files
from portfolio_tracker.backup import make_backup
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.restore import restore

OLD = FIXTURES / "1.0.0-rc.2.sqlite"  # behind the head: its first start takes a copy


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def said(caplog) -> list[str]:
    """The paths the app says it tightened."""
    return [r.path for r in caplog.records if r.name == "portfolio_tracker.private_files"]


@pytest.fixture(params=[0o022, 0o000, 0o077], ids=lambda value: f"umask-{value:03o}")
def umask(request) -> Iterator[int]:
    """Each test runs under the usual umask, under none and under a strict one, and the
    process gets its own umask back: no test depends on the one another test left."""
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


def test_a_new_database_and_its_folder_are_private(tmp_path: Path, umask: int) -> None:
    db_path = tmp_path / "data" / "db" / "app.sqlite"

    engine = create_db_engine(db_path)
    run_migrations(engine, db_path, tmp_path / "data" / "backups" / "pre-migration")
    with engine.connect() as connection:  # open: the -wal and -shm files are there
        connection.execute(text("SELECT 1"))
        beside = sorted(path.name for path in db_path.parent.iterdir())
        modes = {path.name: mode(path) for path in db_path.parent.iterdir()}
    engine.dispose()

    assert beside == ["app.sqlite", "app.sqlite-shm", "app.sqlite-wal"]
    assert set(modes.values()) == {0o600}
    assert mode(db_path.parent) == 0o700


def test_an_existing_installation_is_tightened_at_the_next_start(
    tmp_path: Path, umask: int, caplog
) -> None:
    """A database made by 1.0.0 or earlier: 0644 in a 0755 folder, with a log left beside it.
    Its first upgrade also leaves a private copy in a private folder."""
    data = tmp_path / "data"
    db_path = data / "db" / "app.sqlite"
    db_path.parent.mkdir(parents=True)
    db_path.parent.chmod(0o755)
    shutil.copyfile(OLD, db_path)
    wal = db_path.with_name("app.sqlite-wal")
    wal.touch()
    copies = data / "backups" / "pre-migration"
    copies.mkdir(parents=True)
    for path in (db_path, wal):
        path.chmod(0o644)
    copies.chmod(0o755)

    with caplog.at_level(logging.INFO, logger="portfolio_tracker.private_files"):
        engine = create_db_engine(db_path)
        run_migrations(engine, db_path, copies)
        engine.dispose()

    assert (mode(db_path.parent), mode(db_path), mode(copies)) == (0o700, 0o600, 0o700)
    assert not wal.exists() or mode(wal) == 0o600
    [copy] = copies.glob("app-1000-*.sqlite")
    assert mode(copy) == 0o600
    assert {str(db_path.parent), str(db_path), str(wal), str(copies)} <= set(said(caplog))
    with fixture_app(fixture_settings(data)) as client:  # and the app starts on it
        assert client.get("/api/health").status_code == 200
    assert mode(data / "db" / ".server.lock") == 0o600


def test_the_owner_s_own_access_never_changes(tmp_path: Path) -> None:
    read_only = tmp_path / "file"
    read_only.touch()
    read_only.chmod(0o444)
    folder = tmp_path / "folder"
    folder.mkdir()
    folder.chmod(0o750)

    private_files.restrict(read_only)
    private_files.restrict(folder)
    private_files.restrict(tmp_path / "missing")  # nothing there: nothing happens

    assert (mode(read_only), mode(folder)) == (0o400, 0o700)


def test_what_another_user_owns_is_left_alone(tmp_path: Path, monkeypatch, caplog) -> None:
    """An installation that works through group permissions (the data folder owned by another
    user) keeps working: the app does not try, does not warn and does not fail."""
    db_path = tmp_path / "db" / "app.sqlite"
    db_path.parent.mkdir()
    db_path.parent.chmod(0o775)
    db_path.touch()
    db_path.chmod(0o664)
    monkeypatch.setattr(private_files.os, "geteuid", lambda: os.getuid() + 1)

    with caplog.at_level(logging.INFO, logger="portfolio_tracker.private_files"):
        engine = create_db_engine(db_path)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()

    assert (mode(db_path.parent), mode(db_path)) == (0o775, 0o664)
    assert not said(caplog)


def test_a_file_system_that_refuses_the_change_does_not_stop_the_app(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    def refuse(self, mode):
        raise PermissionError("chmod is not supported here")

    db_path = tmp_path / "db" / "app.sqlite"
    db_path.parent.mkdir()
    db_path.parent.chmod(0o755)
    monkeypatch.setattr(Path, "chmod", refuse)

    with caplog.at_level(logging.INFO, logger="portfolio_tracker.private_files"):
        engine = create_db_engine(db_path)
        run_migrations(engine, db_path, tmp_path / "backups" / "pre-migration")
        engine.dispose()

    assert db_path.stat().st_size > 0 and mode(db_path.parent) == 0o755
    assert not said(caplog)


def test_backup_and_restore_work_on_private_files_and_leave_private_files(
    tmp_path: Path, umask: int
) -> None:
    """The whole round as the app's user: a private database is archived, restored into a
    new folder and started. Every file that holds the data is 0600, every folder 0700."""
    source = tmp_path / "source" / "db" / "app.sqlite"
    engine = create_db_engine(source)
    run_migrations(engine, source, tmp_path / "source" / "backups" / "pre-migration")
    engine.dispose()
    assert mode(source) == 0o600

    archive = make_backup(source, tmp_path / "source" / "backups" / "manual").path
    result = restore(archive, tmp_path / "target")

    assert (mode(archive), mode(archive.parent)) == (0o600, 0o700)
    assert (mode(result.db_path), mode(result.db_path.parent)) == (0o600, 0o700)
    with fixture_app(fixture_settings(tmp_path / "target")) as client:
        assert client.get("/api/health").status_code == 200
    assert mode(result.db_path) == 0o600
