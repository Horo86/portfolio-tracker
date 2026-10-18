"""The app with market data on, for the API tests of market data: a fake provider (invented
Yahoo, OpenFIGI and Frankfurter answers), throttles that never sleep, and a run already logged
today, so no catch-up job runs beside the tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeTransport, InstantTime, Provider
from fastapi.testclient import TestClient
from helpers import create_admin

from portfolio_tracker.config import Settings
from portfolio_tracker.db import db_transaction
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.repositories import job_runs


@pytest.fixture
def answers() -> Provider:
    return Provider()


@pytest.fixture
def on(tmp_path: Path, fast_passwords, clock, answers) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / "data", market_data=True)
    time = InstantTime()
    first = TestClient(create_app(settings.model_copy(update={"market_data": False})))
    with first, db_transaction(first.app.state.engine, write=True) as session:
        run = job_runs.start_run(session, "DAILY", clock.now)
        job_runs.finish_run(session, run.id, result="OK", at=clock.now, counts={}, errors={})
    app = create_app(
        settings,
        passwords=fast_passwords,
        clock=clock,
        market_transport=FakeTransport(answers),
        market_registry=default_registry(time.clock, time.sleep),
    )
    with TestClient(app, base_url="https://testserver") as client:
        create_admin(client)
        yield client
