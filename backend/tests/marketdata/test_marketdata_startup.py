"""Market data at startup (D-121, D-123, D-132): the settings, seeding and the log line when
the environment differs, `PT_MARKET_DATA=off`, the loop started and stopped with the app, the
catch-up on the loop's thread, and runs left by a crash. A fake transport: no network."""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from engines import NOW
from fakes import FakeRequest, FakeTransport, fixture_response
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, select

from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine, db_transaction, models
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.marketdata.routing import ProviderConfigError
from portfolio_tracker.marketdata.throttle import Stopped
from portfolio_tracker.repositories import accounts, job_runs, market_config, users
from portfolio_tracker.services.market_startup import (
    RESET_COMMAND,
    MarketData,
    seed_or_compare,
)
from portfolio_tracker.services.scheduler import Trigger


def loop_alive() -> bool:
    return any(t.name == "market-data" and t.is_alive() for t in threading.enumerate())


@contextmanager
def running(settings: Settings, transport: FakeTransport | None = None) -> Iterator[TestClient]:
    app = create_app(settings, market_transport=transport)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@contextmanager
def database(settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(settings.db_path)
    try:
        yield engine
    finally:
        engine.dispose()


def with_a_usd_account(settings: Settings) -> None:
    """A migrated database with one user holding a USD account: FX would be fetched."""
    with running(settings), database(settings) as engine, db_transaction(engine, write=True) as s:
        alice = users.add_user(
            s, username="alice", role="INVESTOR", password_hash="x", language="en"
        )
        accounts.add_account(s, alice.id, name="Broker", currency="USD")


def refuse(request: FakeRequest):
    return AssertionError(f"unexpected request to {request.url}")


@pytest.fixture
def base(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


# --- settings ---------------------------------------------------------------------------------


def test_market_data_is_on_by_default_and_off_by_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PT_MARKET_DATA")
    assert Settings().market_data is True
    monkeypatch.setenv("PT_MARKET_DATA", "off")
    assert Settings().market_data is False


@pytest.mark.parametrize("value", ["24:00", "7:00", "22:60", "22.00", "22:00:00", ""])
def test_the_daily_time_is_hh_mm(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(market_data_time=value)


def test_the_daily_time() -> None:
    assert Settings().daily_at.isoformat() == "22:00:00"
    assert Settings(market_data_time="06:30").daily_at.isoformat() == "06:30:00"


# --- off ----------------------------------------------------------------------------------------


def test_off_runs_the_housekeeping_only_and_calls_nothing(base: Settings) -> None:
    """The loop runs for the expired-session cleanup; no market job, no outbound call."""
    with_a_usd_account(base)
    with database(base) as engine, db_transaction(engine, write=True) as session:
        alice = session.scalars(select(models.User)).one()
        old = datetime(2020, 1, 1, tzinfo=UTC)
        session.add(
            models.UserSession(
                token_hash="0" * 64,
                user_id=alice.id,
                created_at=old,
                last_used_at=old,
                expires_at=old + timedelta(days=30),
            )
        )
    transport = FakeTransport(refuse)
    with running(base, transport) as client:
        market_data = client.app.state.market_data
        assert loop_alive() and not market_data.on
        assert market_data.scheduler.trigger("MANUAL") is Trigger.OFF
        assert client.get("/api/health").status_code == 200
    assert not loop_alive() and transport.calls == []
    with database(base) as engine, db_transaction(engine, write=False) as session:
        assert market_config.load(session) is not None  # seeded all the same
        assert session.scalars(select(models.JobRun)).all() == []  # no market job
        assert session.scalars(select(models.UserSession)).all() == []  # cleaned up


# --- on -----------------------------------------------------------------------------------------


def test_the_loop_runs_with_the_app_and_the_catch_up_never_delays_the_start(
    base: Settings,
) -> None:
    with_a_usd_account(base)
    asked, release = threading.Event(), threading.Event()

    def frankfurter(request: FakeRequest):
        asked.set()
        assert release.wait(5)
        return fixture_response("frankfurter_v2_rates_usd.json")

    transport = FakeTransport(frankfurter)
    with running(base.model_copy(update={"market_data": True}), transport) as client:
        assert asked.wait(5)  # the catch-up of an empty job log, on the loop's thread
        assert client.get("/api/health").status_code == 200  # while it waits on the network
        assert loop_alive()
        release.set()
    assert not loop_alive()
    assert [request.url for request in transport.calls] == ["https://api.frankfurter.dev/v2/rates"]


def test_runs_left_running_are_interrupted_at_startup(base: Settings) -> None:
    with running(base), database(base) as engine, db_transaction(engine, write=True) as session:
        job_runs.start_run(session, "DAILY", NOW)
    with running(base), database(base) as engine, db_transaction(engine, write=False) as s:
        assert [run.result for run in s.scalars(select(models.JobRun))] == ["INTERRUPTED"]


# --- seeding and the environment ----------------------------------------------------------------


def test_the_environment_is_stored_once_and_a_difference_is_logged(
    db_engine: Engine, base: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    registry = default_registry()
    seed_or_compare(db_engine, registry, base)
    with caplog.at_level(logging.INFO):
        seed_or_compare(db_engine, registry, base)
    assert "differs from the environment" not in caplog.text  # the same values: no line

    with caplog.at_level(logging.INFO):
        seed_or_compare(db_engine, registry, base.model_copy(update={"price_providers": ""}))
    (record,) = [r for r in caplog.records if "differs from the environment" in r.message]
    assert (record.differs, record.reset_command) == (["ETF_STOCK"], RESET_COMMAND)
    with db_transaction(db_engine, write=False) as session:
        assert market_config.load(session).order(Role.ETF_STOCK) == ("yahoo",)  # the database wins


def test_an_invalid_variable_stops_a_first_start_naming_it(base: Settings) -> None:
    wrong = base.model_copy(update={"fx_providers": "yahoo"})  # yahoo serves no FX role
    with pytest.raises(ProviderConfigError, match="PT_FX_PROVIDERS"), running(wrong):
        pass


def test_an_invalid_variable_after_seeding_is_only_logged(
    db_engine: Engine, base: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    registry = default_registry()
    seed_or_compare(db_engine, registry, base)
    with caplog.at_level(logging.WARNING):
        seed_or_compare(db_engine, registry, base.model_copy(update={"price_exchanges": ",,"}))
    (record,) = [r for r in caplog.records if "not valid" in r.message]
    assert "PT_PRICE_EXCHANGES" in record.problem and record.reset_command == RESET_COMMAND


# --- shutdown (final review M-4) -----------------------------------------------------------------


def test_the_real_registry_refuses_provider_calls_once_the_app_stops(base: Settings) -> None:
    """The app's own registry (not one injected by a test) has stop-aware throttles: after
    shutdown no provider call starts, even one that would not have to wait."""
    with running(base.model_copy(update={"market_data": True}), FakeTransport(refuse)) as client:
        registry = client.app.state.market_data.registry
        throttle = registry.throttle("yahoo")
    called: list[str] = []
    with pytest.raises(Stopped):
        throttle.call(lambda: called.append("call"))
    with pytest.raises(Stopped):
        registry.throttle("openfigi").call(lambda: called.append("call"))
    assert called == []


def test_the_database_stays_open_while_the_loop_is_still_busy(
    base: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The app logs JSON to standard output (its own handler replaces pytest's)."""
    disposed: list[bool] = []
    # reported as still busy (a call on the network), though the test's loop does stop
    monkeypatch.setattr(MarketData, "stop", lambda self: self.scheduler.stop(5) and False)
    with running(base) as client:
        monkeypatch.setattr(client.app.state.engine, "dispose", lambda: disposed.append(True))
    assert disposed == [] and "database engine left open" in capsys.readouterr().out

    monkeypatch.undo()
    with running(base) as client:
        monkeypatch.setattr(client.app.state.engine, "dispose", lambda: disposed.append(True))
    assert disposed == [True] and not loop_alive()
