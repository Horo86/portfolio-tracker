"""SQLite engine: one file under the data volume, WAL mode, foreign keys on.

pysqlite's own implicit transactions are disabled so that SQLAlchemy emits the BEGIN itself:
a connection with the execution option `sqlite_begin="IMMEDIATE"` takes the write lock at
the start of the transaction, so a validate-then-write sequence cannot interleave with
another writer (M2 plan).
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event

BEGIN_OPTION = "sqlite_begin"
# How long a writer waits for the write lock before failing (pysqlite's default is 5 s): a
# confirmed import of the largest file holds it for several seconds (D-114).
BUSY_TIMEOUT_SECONDS = 30


def sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def create_db_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # hide_parameters: a database error never quotes the values of its statement (notes,
    # references, amounts of an import), so a logged traceback cannot leak them (D-114).
    engine = create_engine(
        sqlite_url(db_path),
        connect_args={"timeout": BUSY_TIMEOUT_SECONDS},
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record) -> None:
        dbapi_connection.isolation_level = None  # SQLAlchemy emits BEGIN (see `_begin`)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection) -> None:
        mode = connection.get_execution_options().get(BEGIN_OPTION, "DEFERRED")
        if mode not in ("DEFERRED", "IMMEDIATE"):
            raise ValueError(f"unknown SQLite begin mode: {mode}")
        connection.exec_driver_sql(f"BEGIN {mode}")

    return engine
