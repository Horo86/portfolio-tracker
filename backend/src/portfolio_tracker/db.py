"""SQLite engine: one file under the data volume, WAL mode, foreign keys on."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event


def sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(db_path))

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
