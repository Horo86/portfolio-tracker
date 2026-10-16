"""Daily FX update (D-120, D-122) with a fake provider and a migrated database file."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fakes import FakeFxProvider
from sqlalchemy import Engine

from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata.base import ErrorCode, ProviderError
from portfolio_tracker.repositories import accounts, assets, fx, users
from portfolio_tracker.services.fx_rates import (
    fetch_start,
    horizon_start,
    needed_currencies,
    update_fx,
)

TODAY = date(2026, 3, 10)


def seed(engine: Engine) -> None:
    """Invented data: an account in USD, an asset quoted in GBP, a user reporting in CHF."""
    with db_transaction(engine, write=True) as session:
        alice = users.add_user(
            session,
            username="alice",
            role="INVESTOR",
            password_hash="x",
            language="en",
            reporting_currency="CHF",
        )
        accounts.add_account(session, alice.id, name="Broker", currency="USD")
        accounts.add_account(session, alice.id, name="Home", currency="EUR")
        assets.add_private_asset(
            session,
            alice.id,
            name="Example plc",
            asset_type="STOCK",
            quote_currency="GBP",
            quote_unit="MINOR_CURRENCY_UNIT",
        )


def daily(start: date, end: date, rate: str) -> dict[date, Decimal]:
    days = range(start.toordinal(), end.toordinal() + 1)
    return {date.fromordinal(d): Decimal(rate) for d in days if date.fromordinal(d).weekday() < 5}


def test_currencies_in_use_exclude_eur(db_engine: Engine) -> None:
    seed(db_engine)
    with db_transaction(db_engine, write=False) as session:
        assert needed_currencies(session) == {"USD", "GBP", "CHF"}


@pytest.mark.parametrize(
    ("today", "start"),
    [
        (date(2026, 3, 10), date(2006, 3, 10)),
        (date(2028, 2, 29), date(2008, 2, 29)),
        (date(2120, 2, 29), date(2100, 2, 28)),
    ],
)
def test_a_new_series_starts_20_years_back(today: date, start: date) -> None:
    assert horizon_start(today) == start
    assert fetch_start(None, today) == start
    assert fetch_start(date(2026, 3, 3), today) == date(2026, 3, 4)


def test_first_run_fetches_the_horizon_and_a_rerun_fetches_nothing(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider(
        {
            "USD": daily(date(2006, 1, 1), TODAY, "1.1"),
            "GBP": daily(date(2006, 1, 1), TODAY, "0.85"),
            "CHF": daily(date(2006, 1, 1), TODAY, "0.95"),
        }
    )
    first = update_fx(db_engine, provider, TODAY)

    assert sorted(provider.calls) == [
        (("CHF",), date(2006, 3, 10), TODAY),
        (("GBP",), date(2006, 3, 10), TODAY),
        (("USD",), date(2006, 3, 10), TODAY),
    ]
    assert first.failed == {}
    assert first.stored["USD"] == len(daily(date(2006, 3, 10), TODAY, "1"))

    provider.calls.clear()
    again = update_fx(db_engine, provider, TODAY)
    assert provider.calls == [] and again.stored == {} and again.failed == {}


def test_the_next_run_fills_from_the_last_stored_day(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider({"USD": daily(date(2026, 3, 2), date(2026, 3, 3), "1.1")})
    update_fx(db_engine, provider, date(2026, 3, 3))  # GBP and CHF: no data yet
    provider.series["USD"].update(daily(date(2026, 3, 4), TODAY, "1.2"))
    provider.calls.clear()

    result = update_fx(db_engine, provider, TODAY)

    assert (("USD",), date(2026, 3, 4), TODAY) in provider.calls
    assert (("GBP",), date(2006, 3, 10), TODAY) in provider.calls  # still never stored
    assert result.stored == {"USD": 5}
    with db_transaction(db_engine, write=False) as session:
        assert fx.last_stored_day(session, "USD") == TODAY
        assert fx.rates_for(session, {"USD"})["USD"][date(2026, 3, 3)] == Decimal("1.1")


def test_a_failing_currency_stores_nothing_and_the_others_continue(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider(
        {"GBP": daily(date(2026, 3, 2), TODAY, "0.85")},
        fail={"USD": ProviderError(ErrorCode.NETWORK, "TimeoutError")},
    )
    result = update_fx(db_engine, provider, TODAY)

    assert result.failed == {"USD": "NETWORK"}
    assert result.stored == {"GBP": 7}
    with db_transaction(db_engine, write=False) as session:
        assert fx.last_stored_day(session, "USD") is None
        assert fx.last_stored_day(session, "CHF") is None  # not published: no error either


def test_no_transaction_is_open_while_the_provider_is_called(
    db_engine: Engine, tmp_path: Path
) -> None:
    seed(db_engine)
    db_path = tmp_path / "db" / "app.sqlite"
    checked: list[str] = []

    def take_the_write_lock_elsewhere() -> None:
        other = sqlite3.connect(db_path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # fails at once if anyone holds the lock
            other.execute("ROLLBACK")
        finally:
            other.close()
        assert db_engine.pool.checkedout() == 0  # no connection held either
        checked.append("ok")

    provider = FakeFxProvider(
        {"USD": daily(date(2026, 3, 2), TODAY, "1.1")}, during_fetch=take_the_write_lock_elsewhere
    )
    update_fx(db_engine, provider, TODAY)
    assert checked == ["ok", "ok", "ok"]
