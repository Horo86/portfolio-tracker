"""The app with market data on, for the API tests of market data: a fake provider (invented
Yahoo, OpenFIGI and Frankfurter answers), throttles that never sleep, and a run already logged
today, so no catch-up job runs beside the tests."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fakes import FakeTransport, InstantTime, Provider
from fastapi.testclient import TestClient
from helpers import create_admin

from portfolio_tracker.config import Settings
from portfolio_tracker.db import db_transaction
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.registry import Registry, default_registry
from portfolio_tracker.repositories import job_runs


@pytest.fixture
def answers() -> Provider:
    return Provider()


@contextmanager
def market_app(
    data_dir: Path,
    passwords,
    clock,
    answers: Provider,
    registry: Registry | None = None,
    **settings: object,
) -> Iterator[TestClient]:
    """The app with market data on and `settings` (environment values seeded at its first
    start), `registry` or the shipped engines with throttles on an instant clock, and a run
    logged today."""
    config = Settings(data_dir=data_dir, market_data=True, **settings)
    time = InstantTime()
    registry = registry or default_registry(time.clock, time.sleep)
    offline = config.model_copy(update={"market_data": False})
    first = TestClient(create_app(offline, market_registry=registry))
    with first, db_transaction(first.app.state.engine, write=True) as session:
        run = job_runs.start_run(session, "DAILY", clock.now)
        job_runs.finish_run(session, run.id, result="OK", at=clock.now, counts={}, errors={})
    app = create_app(
        config,
        passwords=passwords,
        clock=clock,
        market_transport=FakeTransport(answers),
        market_registry=registry,
    )
    with TestClient(app, base_url="https://testserver") as client:
        create_admin(client)
        yield client


@pytest.fixture
def on(tmp_path: Path, fast_passwords, clock, answers) -> Iterator[TestClient]:
    with market_app(tmp_path / "data", fast_passwords, clock, answers) as client:
        yield client


@pytest.fixture
def market_app_with(tmp_path: Path, fast_passwords, clock):
    """`market_app` for a test that registers its own engines or sets other environment
    values: `with market_app_with(answers, registry, price_providers="...") as client`."""

    def start(answers: Provider, registry: Registry | None = None, **settings: object):
        return market_app(tmp_path / "data", fast_passwords, clock, answers, registry, **settings)

    return start
