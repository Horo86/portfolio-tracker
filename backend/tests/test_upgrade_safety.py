"""The upgrade policy at start (D-168, ADR-0002): free space first, a verified and recorded
pre-migration copy (R-20), no copy for a fresh database or one at the head, a failed migration
rolled back and marked so the same version does not retry in a loop, and the retention of the
copies (R-22). A test-only revision `0007` is added to a copy of the migrations folder; nothing
of it ships in the package."""

import json
import shutil
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker import __version__, migrations_runner
from portfolio_tracker.__main__ import main
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import (
    FAILED_MARK,
    CopyCheckError,
    NotEnoughSpaceError,
    PreviousMigrationFailedError,
    preflight,
    run_migrations,
)

PROBE = '''"""Test-only revision: a table, then {what}."""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("upgrade_probe", sa.Column("id", sa.Integer, primary_key=True))
    {body}


def downgrade() -> None:
    op.drop_table("upgrade_probe")
'''

Probe = Callable[[bool], None]


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Probe:
    """Install revision 0007 (failing or not) in a copy of the migrations folder."""
    folder = tmp_path / "migrations"

    def install(fails: bool) -> None:
        if not folder.exists():
            shutil.copytree(
                migrations_runner.MIGRATIONS_DIR,
                folder,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        body = 'raise RuntimeError("the probe failed on purpose")' if fails else "pass"
        what = "a failure" if fails else "nothing"
        (folder / "versions" / "0007_probe.py").write_text(PROBE.format(body=body, what=what))
        shutil.rmtree(folder / "versions" / "__pycache__", ignore_errors=True)
        monkeypatch.setattr(migrations_runner, "MIGRATIONS_DIR", folder)

    return install


def at_0006(db_path: Path) -> None:
    """A database migrated by the shipped revisions (fresh: no copy)."""
    engine = create_db_engine(db_path)
    run_migrations(engine, db_path, db_path.parent / "unused")
    engine.dispose()


def migrate(db_path: Path, backups: Path) -> Path | None:
    engine = create_db_engine(db_path)
    try:
        return run_migrations(engine, db_path, backups)
    finally:
        engine.dispose()


def revision_of(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def tables_of(db_path: Path) -> set[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}


def dump_of(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        return list(connection.iterdump())


def copies_in(backups: Path) -> list[Path]:
    return sorted(backups.glob("app-*.sqlite")) if backups.exists() else []


def write_mark(db_path: Path, version: str) -> Path:
    mark = db_path.parent / FAILED_MARK
    mark.write_text(
        json.dumps(
            {"version": version, "from": "0006", "to": "0007", "error": "RuntimeError",
             "at": "2026-01-02T03:04:05Z", "copy": None}
        )
    )  # fmt: skip
    return mark


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "data" / "db" / "app.sqlite", tmp_path / "data" / "backups" / "pre-migration"


# --- no copy and no record when nothing is migrated -------------------------------------------


def test_a_fresh_database_takes_no_copy_and_no_record(paths, probe) -> None:
    db_path, backups = paths
    probe(False)

    assert migrate(db_path, backups) is None

    assert revision_of(db_path) == "0007"
    assert not backups.exists()
    assert not (db_path.parent / FAILED_MARK).exists()


def test_a_database_at_the_head_takes_no_copy_and_no_record(paths) -> None:
    db_path, backups = paths
    at_0006(db_path)
    before = dump_of(db_path)

    assert migrate(db_path, backups) is None

    assert not backups.exists()
    assert dump_of(db_path) == before
    preflight(db_path, backups)  # nothing to check either


def test_a_stale_mark_is_cleared_when_the_database_is_at_the_head(paths) -> None:
    db_path, backups = paths
    at_0006(db_path)
    mark = write_mark(db_path, __version__)

    assert migrate(db_path, backups) is None
    assert not mark.exists()


# --- the verified, recorded copy (R-20) ------------------------------------------------------


def test_a_pending_migration_takes_a_verified_and_recorded_copy(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(False)

    copy = migrate(db_path, backups)

    assert copy is not None and copy.parent == backups
    assert copy.name.startswith("app-0006-") and copy.name.endswith("Z.sqlite")
    assert revision_of(copy) == "0006" and "upgrade_probe" not in tables_of(copy)
    assert revision_of(db_path) == "0007" and "upgrade_probe" in tables_of(db_path)
    # One file each, nothing beside them: no .partial, no -wal or -shm of the copy.
    assert sorted(path.name for path in backups.iterdir()) == sorted(
        [copy.name, copy.with_suffix(".json").name]
    )
    record = json.loads(copy.with_suffix(".json").read_text())
    assert record == {
        "from": "0006", "to": "0007", "version": __version__, "at": record["at"],
        "size": copy.stat().st_size, "copy": copy.name, "status": "done",
    }  # fmt: skip
    assert not (db_path.parent / FAILED_MARK).exists()


def test_too_little_space_refuses_before_any_change(paths, probe, monkeypatch) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(False)
    before = dump_of(db_path)
    monkeypatch.setattr(migrations_runner, "free_bytes", lambda folder: 10 * 1024 * 1024)

    with pytest.raises(NotEnoughSpaceError, match="not enough free space") as error:
        preflight(db_path, backups)
    assert "MiB are free" in str(error.value)
    with pytest.raises(NotEnoughSpaceError):
        migrate(db_path, backups)

    assert not backups.exists()
    assert dump_of(db_path) == before
    assert not (db_path.parent / FAILED_MARK).exists()


def test_a_copy_that_fails_its_check_migrates_nothing(paths, probe, monkeypatch) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(False)
    before = dump_of(db_path)

    def broken_copy(source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not a database, only some bytes " * 100)

    monkeypatch.setattr(migrations_runner, "copy_database", broken_copy)

    with pytest.raises(CopyCheckError, match="nothing was upgraded"):
        migrate(db_path, backups)

    assert list(backups.iterdir()) == []  # neither the copy nor its .partial file
    assert dump_of(db_path) == before
    assert not (db_path.parent / FAILED_MARK).exists()


def test_a_copy_at_another_revision_fails_its_check(paths, probe, monkeypatch) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(False)
    real_copy = migrations_runner.copy_database

    def copy_then_restamp(source: Path, target: Path) -> None:
        real_copy(source, target)
        with closing(sqlite3.connect(target)) as connection:
            connection.execute("UPDATE alembic_version SET version_num = '0005'")
            connection.commit()

    monkeypatch.setattr(migrations_runner, "copy_database", copy_then_restamp)

    with pytest.raises(CopyCheckError, match="at revision 0005, not 0006"):
        migrate(db_path, backups)
    assert revision_of(db_path) == "0006"
    assert copies_in(backups) == []


# --- a failed migration: rolled back, marked, not retried by the same version ----------------


def test_a_failed_migration_is_rolled_back_marked_and_not_retried(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(True)
    before = dump_of(db_path)

    with pytest.raises(RuntimeError, match="failed on purpose"):
        migrate(db_path, backups)

    assert revision_of(db_path) == "0006" and "upgrade_probe" not in tables_of(db_path)
    assert dump_of(db_path) == before
    [copy] = copies_in(backups)
    assert json.loads(copy.with_suffix(".json").read_text())["status"] == "failed"
    mark = db_path.parent / FAILED_MARK
    assert json.loads(mark.read_text()) == {
        "version": __version__, "from": "0006", "to": "0007", "error": "RuntimeError",
        "at": json.loads(mark.read_text())["at"], "copy": copy.name,
    }  # fmt: skip

    # Started again by the same version: refused at once, no new copy, and it says how to retry.
    for check in (lambda: preflight(db_path, backups), lambda: migrate(db_path, backups)):
        with pytest.raises(PreviousMigrationFailedError) as error:
            check()
        message = str(error.value)
        assert str(mark) in message and FAILED_MARK in message
        assert "read the error in the log, then delete" in message
        assert "left unchanged at revision 0006" in message
    assert copies_in(backups) == [copy]
    assert dump_of(db_path) == before


def test_deleting_the_mark_lets_the_same_version_try_again(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(True)
    with pytest.raises(RuntimeError):
        migrate(db_path, backups)

    (db_path.parent / FAILED_MARK).unlink()  # the documented way to retry
    probe(False)  # e.g. the temporary problem is gone
    assert migrate(db_path, backups) is not None
    assert revision_of(db_path) == "0007"


def test_another_version_clears_the_mark_and_tries_again(paths, probe, monkeypatch) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(True)
    with pytest.raises(RuntimeError):
        migrate(db_path, backups)
    mark = db_path.parent / FAILED_MARK

    probe(False)
    monkeypatch.setattr(migrations_runner, "__version__", "9.9.9")  # a fixed release
    preflight(db_path, backups)  # leaves the mark for the runner
    assert mark.exists()

    copy = migrate(db_path, backups)

    assert copy is not None and revision_of(db_path) == "0007"
    assert not mark.exists()
    assert json.loads(copy.with_suffix(".json").read_text())["version"] == "9.9.9"
    assert len(copies_in(backups)) == 2


def test_an_unreadable_mark_refuses_like_the_same_version(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    probe(False)
    (db_path.parent / FAILED_MARK).write_text("{ not json")

    with pytest.raises(PreviousMigrationFailedError, match=FAILED_MARK):
        migrate(db_path, backups)
    assert revision_of(db_path) == "0006"


# --- retention (R-22) ------------------------------------------------------------------------


def old_copies(db_path: Path, backups: Path) -> list[Path]:
    """Five copies as 0.9.0 named them (no fraction), the first two with a record file."""
    backups.mkdir(parents=True)
    made = []
    for day in range(1, 6):
        path = backups / f"app-0005-2026010{day}T000000Z.sqlite"
        shutil.copyfile(db_path, path)
        if day <= 2:
            path.with_suffix(".json").write_text("{}")
        made.append(path)
    return made


def test_a_successful_migration_keeps_the_three_newest_copies(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    old = old_copies(db_path, backups)
    (backups / "app-0006-20260106T000000Z.sqlite.partial").write_text("half")
    (backups / "notes.txt").write_text("mine")
    (backups / "app-by-hand.sqlite").write_text("mine too")
    probe(False)

    copy = migrate(db_path, backups)

    assert copy is not None
    kept = {path.name for path in backups.iterdir()}
    assert kept == {
        old[3].name, old[4].name, copy.name, copy.with_suffix(".json").name,
        "notes.txt", "app-by-hand.sqlite",
    }  # fmt: skip


def test_a_failed_migration_deletes_no_copy(paths, probe) -> None:
    db_path, backups = paths
    at_0006(db_path)
    old = old_copies(db_path, backups)
    probe(True)

    with pytest.raises(RuntimeError):
        migrate(db_path, backups)

    assert set(old) < set(copies_in(backups)) and len(copies_in(backups)) == 6


# --- the server and the commands -------------------------------------------------------------


def test_the_server_and_the_commands_print_the_refusals_without_a_traceback(
    monkeypatch, tmp_path: Path, capsys, probe
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))
    settings = Settings()
    at_0006(settings.db_path)
    probe(False)
    started = []
    monkeypatch.setattr("portfolio_tracker.__main__.serve", lambda s: started.append(s))
    commands = ([], ["reset-password", "admin"], ["reset-market-data-config"])

    mark = write_mark(settings.db_path, __version__)
    for argv in commands:
        assert main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith("error: the upgrade of the database"), argv
        assert FAILED_MARK in err and "Traceback" not in err, argv

    mark.unlink()
    monkeypatch.setattr(migrations_runner, "free_bytes", lambda folder: 0)
    for argv in commands:
        assert main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith("error: not enough free space"), argv
        assert "Traceback" not in err, argv

    assert started == []
    assert revision_of(settings.db_path) == "0006"
    assert not settings.pre_migration_backup_dir.exists()


def test_the_app_does_not_start_after_a_failed_migration(tmp_path: Path, probe) -> None:
    """Nothing is served: the lifespan refuses before the first request."""
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    at_0006(settings.db_path)
    probe(False)
    write_mark(settings.db_path, __version__)

    with pytest.raises(PreviousMigrationFailedError), TestClient(create_app(settings)) as client:
        client.get("/api/health")

    assert revision_of(settings.db_path) == "0006"


# --- the released fixture behind the head: what the first migration after it will do --------


def test_the_released_fixture_takes_a_copy_and_keeps_its_figures_when_behind(
    tmp_path: Path, probe
) -> None:
    from test_upgrade_paths import (
        FIXTURES,
        assert_contains,
        fixture_app,
        fixture_settings,
        index,
        snapshot,
    )

    entry = next(entry for entry in reversed(index()) if "file" in entry)
    settings = fixture_settings(tmp_path / "data")
    settings.db_path.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / entry["file"], settings.db_path)
    probe(False)  # a no-op revision on top of the released head

    with fixture_app(settings) as client:
        actual = snapshot(client)

    assert revision_of(settings.db_path) == "0007"
    [copy] = copies_in(settings.pre_migration_backup_dir)
    assert revision_of(copy) == entry["revision"]
    assert json.loads(copy.with_suffix(".json").read_text())["status"] == "done"
    assert_contains(json.loads((FIXTURES / entry["expected"]).read_text("utf-8")), actual)
