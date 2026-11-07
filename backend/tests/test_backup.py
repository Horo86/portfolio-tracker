"""The backup command (D-169, R-21, ADR-0008; `docs/backup-format.md`).

The live database is a copy of the newest upgrade fixture (invented data, `ZZ` ISINs, one
provider credential under the fake test key), with sessions opened by logging in. The archive
must hold the data and the password hashes, and none of the access material: no provider
credential and no session, neither as rows nor as bytes left in free pages.
"""

import hashlib
import io
import json
import sqlite3
import stat
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
from test_upgrade_paths import CSRF, FAKE_CREDENTIAL, INVESTOR, fixture_app

from portfolio_tracker import __version__, backup, migrations_runner
from portfolio_tracker.__main__ import main
from portfolio_tracker.backup import BACKUP_EXCLUDED, BackupError, make_backup
from portfolio_tracker.config import Settings
from portfolio_tracker.migrations_runner import NewerDatabaseError, NotEnoughSpaceError


def live_files(db_path: Path) -> bytes:
    """Every byte of the live database: the file and its write-ahead log."""
    wal = db_path.with_name(db_path.name + "-wal")
    return db_path.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def dump_of(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        return list(connection.iterdump())


def rows(db_path: Path, query: str) -> list[tuple]:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(query).fetchall()


def opened(archive: Path) -> tuple[zipfile.ZipFile, dict, bytes]:
    zf = zipfile.ZipFile(archive)
    return zf, json.loads(zf.read("manifest.json")), zf.read("db/app.sqlite")


def test_the_archive_holds_the_data_and_its_manifest(live: Settings) -> None:
    before = dump_of(live.db_path)

    result = make_backup(live.db_path, live.manual_backup_dir)

    assert result.path.parent == live.manual_backup_dir
    assert result.path.name.startswith(f"portfolio-{__version__}-") and result.path.suffix == ".zip"
    assert list(live.manual_backup_dir.iterdir()) == [result.path]  # nothing left beside it
    zf, manifest, db_bytes = opened(result.path)
    assert sorted(zf.namelist()) == ["attachments/", "db/app.sqlite", "manifest.json"]
    assert manifest == {
        "format": 1, "app": "portfolio-tracker", "app_version": __version__,
        "schema_revision": "0006", "created_at": manifest["created_at"], "encrypted": False,
        "files": [{"path": "db/app.sqlite", "size": len(db_bytes),
                   "sha256": hashlib.sha256(db_bytes).hexdigest()}],
        "excluded": {"tables": ["provider_credentials", "sessions"], "paths": ["secrets/"]},
    }  # fmt: skip
    assert result.schema_revision == "0006" and result.size == result.path.stat().st_size

    copy = live.data_dir.parent / "restored.sqlite"
    copy.write_bytes(db_bytes)
    assert rows(copy, "PRAGMA integrity_check") == [("ok",)]
    for table in ("users", "accounts", "assets", "transactions", "prices", "instrument_prices",
                  "fx_rates", "import_batches", "audit_log"):  # fmt: skip
        assert rows(copy, f"SELECT count(*) FROM {table}") == rows(
            live.db_path, f"SELECT count(*) FROM {table}"
        ), table
    # The password hashes stay: a restored instance logs in by password.
    assert rows(copy, "SELECT username, password_hash FROM users ORDER BY username") == rows(
        live.db_path, "SELECT username, password_hash FROM users ORDER BY username"
    )
    assert dump_of(live.db_path) == before  # the live database is not changed


def test_no_credential_and_no_session_of_the_live_database_is_in_the_archive(
    live: Settings,
) -> None:
    [(nonce, ciphertext)] = rows(live.db_path, "SELECT nonce, ciphertext FROM provider_credentials")
    sessions = rows(live.db_path, "SELECT id, token_hash, user_agent FROM sessions")
    assert len(sessions) >= 2
    secrets = [nonce, ciphertext, FAKE_CREDENTIAL[1].encode()]
    for session_id, token_hash, _agent in sessions:
        secrets += [session_id.encode(), token_hash.encode()]
    live_bytes = live_files(live.db_path)
    assert ciphertext in live_bytes and all(s[1].encode() in live_bytes for s in sessions)

    result = make_backup(live.db_path, live.manual_backup_dir)

    zf, _manifest, db_bytes = opened(result.path)
    raw = result.path.read_bytes()
    every_entry = b"".join(zf.read(name) for name in zf.namelist())
    for secret in secrets:
        assert secret not in db_bytes and secret not in every_entry and secret not in raw
    copy = live.data_dir.parent / "restored.sqlite"
    copy.write_bytes(db_bytes)
    for table in BACKUP_EXCLUDED.tables:
        assert rows(copy, f"SELECT count(*) FROM {table}") == [(0,)], table
    assert rows(copy, "PRAGMA freelist_count") == [(0,)]  # vacuumed: no free page left
    assert not any(name.startswith("secrets") for name in zf.namelist())
    # The live database still has them.
    assert rows(live.db_path, "SELECT nonce, ciphertext FROM provider_credentials") == [
        (nonce, ciphertext)
    ]
    assert rows(live.db_path, "SELECT id, token_hash, user_agent FROM sessions") == sessions


def test_the_archive_and_its_folder_are_private(live: Settings) -> None:
    result = make_backup(live.db_path, live.manual_backup_dir)
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(live.manual_backup_dir.stat().st_mode) == 0o700


def test_a_backup_works_while_the_server_runs(live: Settings) -> None:
    with fixture_app(live) as client:
        client.post("/api/v1/auth/login", headers=CSRF,
                    json={"username": INVESTOR[0], "password": INVESTOR[1]})  # fmt: skip
        result = make_backup(live.db_path, live.manual_backup_dir)
        assert client.get("/api/v1/portfolio/summary").status_code == 200
    _, manifest, _ = opened(result.path)
    assert manifest["schema_revision"] == "0006"


def test_an_older_database_is_backed_up_as_it_is(tmp_path: Path) -> None:
    from alembic import command

    db_path = tmp_path / "data" / "db" / "app.sqlite"
    engine = migrations_runner.create_db_engine(db_path)
    config = migrations_runner.alembic_config(db_path)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0005")
    engine.dispose()
    before = dump_of(db_path)

    result = make_backup(db_path, tmp_path / "out")

    assert result.schema_revision == "0005"
    assert dump_of(db_path) == before  # nothing migrated
    assert not (tmp_path / "data" / "backups").exists()  # and no pre-migration copy


@pytest.mark.parametrize("problem", ["missing", "newer", "space"])
def test_refusals_leave_nothing_behind(live: Settings, monkeypatch, problem: str) -> None:
    output = live.manual_backup_dir
    if problem == "missing":
        live.db_path.unlink()
        expected = BackupError
    elif problem == "newer":
        with closing(sqlite3.connect(live.db_path)) as connection:
            connection.execute("UPDATE alembic_version SET version_num = '9999'")
            connection.commit()
        expected = NewerDatabaseError
    else:
        monkeypatch.setattr(migrations_runner, "free_bytes", lambda folder: 1024)
        expected = NotEnoughSpaceError
    before = dump_of(live.db_path) if live.db_path.exists() else None

    with pytest.raises(expected) as error:
        make_backup(live.db_path, output)

    assert not output.exists()
    if problem == "space":
        assert "not enough free space for the backup" in str(error.value)
    if before is not None:
        assert dump_of(live.db_path) == before


def test_a_failure_while_writing_leaves_no_partial_file(live: Settings, monkeypatch) -> None:
    def broken(*args, **kwargs):
        raise OSError("disk full, on purpose")

    monkeypatch.setattr(backup.zipfile.ZipFile, "write", broken)

    with pytest.raises(OSError, match="on purpose"):
        make_backup(live.db_path, live.manual_backup_dir)
    assert list(live.manual_backup_dir.iterdir()) == []


def test_an_archive_that_does_not_read_back_is_refused(live: Settings, monkeypatch) -> None:
    real = backup._sha256
    monkeypatch.setattr(backup, "_sha256", lambda path: "0" * 64 if path.suffix else real(path))

    with pytest.raises(BackupError, match="does not match its checksum"):
        make_backup(live.db_path, live.manual_backup_dir)
    assert list(live.manual_backup_dir.iterdir()) == []


def test_the_command_writes_the_archive_and_says_it_is_not_encrypted(
    live: Settings, monkeypatch, capsys, tmp_path: Path
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(live.data_dir))
    monkeypatch.setenv("PT_SECRET_KEY", live.secret_key.get_secret_value())

    assert main(["backup"]) == 0
    out = capsys.readouterr().out
    [archive] = live.manual_backup_dir.glob("*.zip")
    assert f"Backup written: {archive}" in out and "schema 0006" in out
    assert "NOT encrypted" in out and "Copy it away from this server" in out
    assert "enter the credentials again" in out

    elsewhere = tmp_path / "elsewhere"
    assert main(["backup", "--output", str(elsewhere)]) == 0
    assert len(list(elsewhere.glob("portfolio-*.zip"))) == 1
    capsys.readouterr()

    live.db_path.unlink()
    assert main(["backup"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: there is no database") and "Traceback" not in err


def test_the_manifest_is_valid_json_with_sorted_keys(live: Settings) -> None:
    result = make_backup(live.db_path, live.manual_backup_dir)
    text = zipfile.ZipFile(result.path).read("manifest.json").decode()
    assert text == json.dumps(json.load(io.StringIO(text)), indent=2, sort_keys=True) + "\n"
