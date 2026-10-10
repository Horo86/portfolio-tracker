"""Alembic environment. The app passes its own connection; the CLI uses PT_DATA_DIR."""

from alembic import context
from sqlalchemy import create_engine

from portfolio_tracker.config import Settings
from portfolio_tracker.db import sqlite_url

config = context.config
target_metadata = None  # models arrive in M2


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    url = config.get_main_option("sqlalchemy.url") or sqlite_url(Settings().db_path)
    engine = create_engine(url)
    with engine.begin() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
