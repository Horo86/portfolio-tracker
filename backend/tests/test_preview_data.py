"""Data of a preview version is refused untouched (D-173).

0.9.0 and 1.0.0-rc.1 wrote the schema of the preview revisions `0001` to `0006`, which the
baseline `1000` replaced; their data is never upgraded. The start, every command and the
restore stop with "start from an empty folder", and the data folder stays byte for byte as it
was: no copy, no failure mark, no lock file, no key, no write-ahead files. The database is the
0.9.0 fixture, invented data made with the code of that tag (`tests/fixtures/preview/`).
"""

import hashlib
import json
import shutil
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker.__main__ import main
from portfolio_tracker.backup import BackupError, make_backup
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import (
    PreviewDatabaseError,
    preflight,
    refuse_newer,
    run_migrations,
)
from portfolio_tracker.restore import RestoreError, restore

PREVIEW = Path(__file__).parent / "fixtures" / "preview" / "0.9.0.sqlite"
MESSAGE = "comes from a preview version"


def preview_folder(tmp_path: Path, *, wal: bool = False) -> Settings:
    """A data folder written by 0.9.0: its database, and the key 0.9.0 made at its first start.
    With `wal`, the database is in WAL mode, as a stopped preview container leaves it."""
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    settings.db_path.parent.mkdir(parents=True)
    shutil.copyfile(PREVIEW, settings.db_path)
    if wal:
        with closing(sqlite3.connect(settings.db_path)) as connection:
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        assert not settings.db_path.with_name("app.sqlite-wal").exists()
    (settings.data_dir / "secrets").mkdir()
    (settings.data_dir / "secrets" / "secret.key").write_text("made by the preview\n")
    return settings


def folder_state(data_dir: Path) -> dict[str, str]:
    """Every file of the data folder and its SHA-256: anything written shows up here."""
    return {
        str(path.relative_to(data_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(data_dir.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("wal", [False, True], ids=["rollback-journal", "wal"])
def test_the_runner_and_the_preflight_refuse_a_preview_database_untouched(
    tmp_path: Path, wal: bool
) -> None:
    settings = preview_folder(tmp_path, wal=wal)
    before = folder_state(settings.data_dir)

    with pytest.raises(PreviewDatabaseError, match=MESSAGE) as error:
        preflight(settings.db_path, settings.pre_migration_backup_dir)
    engine = create_db_engine(settings.db_path)
    with pytest.raises(PreviewDatabaseError, match=MESSAGE):
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
    engine.dispose()
    with pytest.raises(PreviewDatabaseError, match=MESSAGE):
        refuse_newer(settings.db_path)  # the backup's check

    message = str(error.value)
    assert "start from an empty folder" in message and "revision 0006" in message
    assert "was not changed" in message
    assert folder_state(settings.data_dir) == before


def test_a_log_left_by_a_crash_is_read_and_the_database_still_refused(tmp_path: Path) -> None:
    """With a `-wal` file beside it, the revision is read through that log, which stays as it
    was: the preview's own next start would apply it."""
    settings = preview_folder(tmp_path)
    work = tmp_path / "work.sqlite"
    shutil.copyfile(PREVIEW, work)
    connection = sqlite3.connect(work)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("UPDATE users SET language = language")  # a frame in the log
    connection.commit()
    for suffix in ("", "-wal"):  # the files of a process that stopped without a checkpoint
        shutil.copyfile(f"{work}{suffix}", f"{settings.db_path}{suffix}")
    connection.close()
    database = settings.db_path.read_bytes()
    log = settings.db_path.with_name("app.sqlite-wal").read_bytes()

    with pytest.raises(PreviewDatabaseError, match=MESSAGE):
        preflight(settings.db_path, settings.pre_migration_backup_dir)

    assert settings.db_path.read_bytes() == database
    assert settings.db_path.with_name("app.sqlite-wal").read_bytes() == log
    assert not settings.pre_migration_backup_dir.exists()


def test_the_app_does_not_start_on_a_preview_folder(tmp_path: Path) -> None:
    settings = preview_folder(tmp_path, wal=True)
    before = folder_state(settings.data_dir)

    with pytest.raises(PreviewDatabaseError, match=MESSAGE), TestClient(create_app(settings)):
        pass

    assert folder_state(settings.data_dir) == before  # no lock file, no key, no copy


@pytest.mark.parametrize(
    "argv",
    [[], ["reset-password", "admin"], ["reset-market-data-config"], ["backup"]],
    ids=["start", "reset-password", "reset-market-data-config", "backup"],
)
def test_every_command_refuses_a_preview_folder_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, argv: list[str]
) -> None:
    settings = preview_folder(tmp_path, wal=True)
    monkeypatch.setenv("PT_DATA_DIR", str(settings.data_dir))
    started = []
    monkeypatch.setattr("portfolio_tracker.__main__.serve", lambda s: started.append(s))
    before = folder_state(settings.data_dir)

    assert main(argv) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: this data folder comes from a preview version"), err
    assert "start from an empty folder" in err and "Traceback" not in err
    assert started == []
    assert folder_state(settings.data_dir) == before


def test_the_backup_of_a_preview_folder_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    settings = preview_folder(tmp_path)
    before = folder_state(settings.data_dir)

    with pytest.raises((BackupError, PreviewDatabaseError), match=MESSAGE):
        make_backup(settings.db_path, settings.manual_backup_dir)

    assert folder_state(settings.data_dir) == before


def preview_archive(tmp_path: Path) -> Path:
    """An archive as 1.0.0-rc.1's `backup` wrote it: the preview database, manifest at 0006."""
    database = PREVIEW.read_bytes()
    manifest = {
        "format": 1,
        "app": "portfolio-tracker",
        "app_version": "1.0.0rc1",
        "schema_revision": "0006",
        "created_at": "2026-01-02T03:04:05Z",
        "encrypted": False,
        "files": [
            {
                "path": "db/app.sqlite",
                "size": len(database),
                "sha256": hashlib.sha256(database).hexdigest(),
            }
        ],
        "excluded": ["provider credentials", "sessions", "secrets"],
    }
    archive = tmp_path / "portfolio-1.0.0rc1-20260102T030405Z.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("db/app.sqlite", database)
        zf.writestr(zipfile.ZipInfo("attachments/"), b"")
    return archive


def test_an_archive_of_a_preview_is_not_restored(tmp_path: Path, capsys) -> None:
    target = tmp_path / "new"

    with pytest.raises(RestoreError, match="the archive comes from a preview version"):
        restore(preview_archive(tmp_path), target)

    assert not (target / "db" / "app.sqlite").exists()
    assert not list(target.rglob("*.partial"))


def test_restore_refuses_a_preview_folder_as_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    settings = preview_folder(tmp_path)
    monkeypatch.setenv("PT_DATA_DIR", str(settings.data_dir))
    before = folder_state(settings.data_dir)

    assert main(["restore", str(preview_archive(tmp_path))]) == 1
    assert "comes from a preview version" in capsys.readouterr().err
    assert folder_state(settings.data_dir) == before
