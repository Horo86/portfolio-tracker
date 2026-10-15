from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from portfolio_tracker.auth.passwords import Passwords
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import run_migrations


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


class Clock:
    """Controllable time for session expiry and login throttling tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@pytest.fixture(scope="session")
def fast_passwords() -> Passwords:
    """Cheap Argon2id parameters: tests only, never configurable in the app."""
    return Passwords(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def app(settings: Settings, fast_passwords: Passwords, clock: Clock) -> FastAPI:
    return create_app(settings, passwords=fast_passwords, clock=clock)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
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
