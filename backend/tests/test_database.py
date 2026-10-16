import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import text

from portfolio_tracker.db import create_db_engine
from portfolio_tracker.migrations_runner import run_migrations


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
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0004",)


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
    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = create_db_engine(tmp_path / "db.sqlite")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE t (v TEXT UNIQUE)"))
        connection.execute(text("INSERT INTO t VALUES (:v)"), {"v": "Example note 1234.56"})
    with pytest.raises(IntegrityError) as exc, engine.begin() as connection:
        connection.execute(text("INSERT INTO t VALUES (:v)"), {"v": "Example note 1234.56"})
    assert "1234.56" not in str(exc.value) and "hidden" in str(exc.value)
    engine.dispose()
