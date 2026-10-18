"""Alembic environment. The app passes its own connection, with foreign keys already off;
the command line uses PT_DATA_DIR (or `sqlalchemy.url`) and turns them off the same way, so a
table rebuilt in batch mode never deletes or blocks the rows that reference it (e.g. `prices`
when 0004 rebuilds `assets`)."""

from alembic import context
from sqlalchemy import create_engine

from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.db.models import Base
from portfolio_tracker.migrations_runner import without_foreign_keys

config = context.config
target_metadata = Base.metadata


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    url = config.get_main_option("sqlalchemy.url")
    engine = create_engine(url) if url else create_db_engine(Settings().db_path)
    try:
        with engine.connect() as conn, without_foreign_keys(conn):
            context.configure(
                connection=conn, target_metadata=target_metadata, render_as_batch=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
