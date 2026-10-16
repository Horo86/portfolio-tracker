import ipaddress
import socket
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

# --- Network guard: tests never leave the machine (D-117) -----------------------------------


class NetworkBlockedError(RuntimeError):
    """A test tried to resolve or reach a non-local address."""


def _is_local_host(host: object) -> bool:
    if host is None or host == "localhost":
        return True
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_local_address(family: int, address: object) -> bool:
    if family == getattr(socket, "AF_UNIX", None):
        return True
    return isinstance(address, tuple) and _is_local_host(address[0])


_real_getaddrinfo = socket.getaddrinfo
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_getaddrinfo(host: str | bytes | None, *args: object, **kwargs: object) -> list:
    if not _is_local_host(host.decode() if isinstance(host, bytes) else host):
        raise NetworkBlockedError(f"tests must not resolve {host!r}")
    return _real_getaddrinfo(host, *args, **kwargs)


def _guarded_connect(self: socket.socket, address: object) -> None:
    if not _is_local_address(self.family, address):
        raise NetworkBlockedError(f"tests must not connect to {address!r}")
    return _real_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: object) -> int:
    if not _is_local_address(self.family, address):
        raise NetworkBlockedError(f"tests must not connect to {address!r}")
    return _real_connect_ex(self, address)


@pytest.fixture(autouse=True, scope="session")
def network_guard() -> Iterator[None]:
    """Any DNS lookup or connection to a non-loopback address fails loudly. The TestClient
    runs in process and local test servers bind to 127.0.0.1, so both still work."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
        patch.setattr(socket.socket, "connect", _guarded_connect)
        patch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
        yield


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
    # https: the session cookie is Secure by default and would not be sent over http.
    with TestClient(app, base_url="https://testserver") as test_client:
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
