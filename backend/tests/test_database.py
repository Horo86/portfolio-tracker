import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from portfolio_tracker.__main__ import main
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import (
    NewerDatabaseError,
    check_database_known,
    run_migrations,
)


def test_engine_uses_wal_and_foreign_keys(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "db" / "app.sqlite")

    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_new_database_is_migrated_without_copy(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    backups = tmp_path / "backups"

    copy = run_migrations(create_db_engine(db_path), db_path, backups)

    assert copy is None
    assert not backups.exists()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0006",)


def test_existing_database_with_pending_revision_is_copied_first(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    db_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.execute("INSERT INTO marker VALUES ('before migration')")
        connection.commit()

    copy = run_migrations(create_db_engine(db_path), db_path, tmp_path / "backups")

    assert copy is not None and copy.name.startswith("app-base-")
    with closing(sqlite3.connect(copy)) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("before migration",)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "alembic_version" not in tables


def test_up_to_date_database_takes_no_copy(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    backups = tmp_path / "backups"
    run_migrations(create_db_engine(db_path), db_path, backups)

    assert run_migrations(create_db_engine(db_path), db_path, backups) is None
    assert not backups.exists()


def test_database_errors_never_quote_statement_values(tmp_path: Path) -> None:
    """D-114: a logged database error must not carry imported notes, references or amounts."""
    from sqlalchemy.exc import IntegrityError

    engine = create_db_engine(tmp_path / "db.sqlite")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (v TEXT UNIQUE)"))
        connection.execute(text("INSERT INTO t VALUES (:v)"), {"v": "Example note 1234.56"})
    with pytest.raises(IntegrityError) as exc, engine.begin() as connection:
        connection.execute(text("INSERT INTO t VALUES (:v)"), {"v": "Example note 1234.56"})
    assert "1234.56" not in str(exc.value) and "hidden" in str(exc.value)
    engine.dispose()


# --- R-18: a database written by a newer version is refused before any copy -----------------


def newer_database(db_path: Path) -> list[str]:
    """A migrated database stamped with a revision no version knows yet; returns its dump."""
    engine = create_db_engine(db_path)
    run_migrations(engine, db_path, db_path.parent / "unused")
    engine.dispose()
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '9999'")
        connection.commit()
        return list(connection.iterdump())


def dump_of(db_path: Path) -> list[str]:
    with closing(sqlite3.connect(db_path)) as connection:
        return list(connection.iterdump())


def test_newer_database_is_refused_before_the_copy(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "app.sqlite"
    before = newer_database(db_path)
    backups = tmp_path / "backups"
    engine = create_db_engine(db_path)

    with pytest.raises(NewerDatabaseError) as error:
        run_migrations(engine, db_path, backups)
    engine.dispose()

    message = str(error.value)
    assert "revision 9999" in message and "the newest it knows is 0006" in message
    assert "start the newer image again" in message and "copy of the data folder" in message
    assert not backups.exists()
    assert dump_of(db_path) == before


def test_check_database_known_passes_a_missing_an_older_and_a_current_database(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing" / "app.sqlite"
    check_database_known(missing)
    assert not missing.exists()

    older = tmp_path / "older" / "app.sqlite"
    older.parent.mkdir()
    with closing(sqlite3.connect(older)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT)")
        connection.commit()
    check_database_known(older)

    current = tmp_path / "current" / "app.sqlite"
    run_migrations(create_db_engine(current), current, tmp_path / "backups")
    check_database_known(current)


def test_the_server_and_the_commands_refuse_a_newer_database_without_a_traceback(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))
    settings = Settings()
    before = newer_database(settings.db_path)
    started = []
    monkeypatch.setattr("portfolio_tracker.__main__.serve", lambda s: started.append(s))

    for argv in ([], ["reset-password", "admin"], ["reset-market-data-config"]):
        assert main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith("error: the database is at revision 9999"), argv
        assert "Traceback" not in err, argv

    assert started == []
    assert not settings.pre_migration_backup_dir.exists()
    assert dump_of(settings.db_path) == before


def test_the_app_start_refuses_a_newer_database(tmp_path: Path) -> None:
    """The lifespan's own check, for a start that skips the command line's."""
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    before = newer_database(settings.db_path)

    with pytest.raises(NewerDatabaseError), TestClient(create_app(settings)):
        pass

    assert not settings.pre_migration_backup_dir.exists()
    assert dump_of(settings.db_path) == before
