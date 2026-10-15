from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import run_migrations


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def db_engine(tmp_path: Path) -> Iterator[Engine]:
    """A migrated database file, without the web app."""
    db_path = tmp_path / "db" / "app.sqlite"
    engine = create_db_engine(db_path)
    run_migrations(engine, db_path, tmp_path / "backups")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    with Session(db_engine, expire_on_commit=False) as session, session.begin():
        yield session
