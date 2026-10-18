"""Daily FX update (D-120, D-122) with a fake provider and a migrated database file."""

import sqlite3
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fakes import FX_CAPABILITIES, FakeFxProvider
from sqlalchemy import Engine, select

from portfolio_tracker.db import db_transaction, models
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
    assert fetch_start(date(2026, 2, 27), today) == date(2026, 2, 27)  # its overlap again


def test_an_engine_with_a_shorter_history_starts_later() -> None:
    assert fetch_start(None, TODAY, history_years=5) == date(2021, 3, 10)
    assert fetch_start(None, TODAY, history_years=30) == date(2006, 3, 10)  # still capped
    assert fetch_start(date(2026, 2, 27), TODAY, history_years=5) == date(2026, 2, 27)


def test_first_run_fetches_the_horizon_and_a_rerun_fetches_nothing(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider(
        {
            "USD": daily(date(2006, 1, 1), TODAY, "1.1"),
            "GBP": daily(date(2006, 1, 1), TODAY, "0.85"),
            "CHF": daily(date(2006, 1, 1), TODAY, "0.95"),
        }
    )
    first = update_fx(db_engine, [provider], TODAY)

    assert sorted(provider.calls) == [
        (("CHF",), date(2006, 3, 10), TODAY),
        (("GBP",), date(2006, 3, 10), TODAY),
        (("USD",), date(2006, 3, 10), TODAY),
    ]
    assert first.failed == {}
    assert first.stored["USD"] == len(daily(date(2006, 3, 10), TODAY, "1"))

    provider.calls.clear()
    with db_transaction(db_engine, write=False) as session:
        before = fx.rates_for(session, {"USD", "GBP", "CHF"})
    again = update_fx(db_engine, [provider], TODAY)
    assert sorted(provider.calls) == [  # only the last 5 stored days, across the weekend
        ((c,), date(2026, 3, 4), TODAY) for c in ("CHF", "GBP", "USD")
    ]
    assert again.failed == {}
    with db_transaction(db_engine, write=False) as session:
        assert fx.rates_for(session, {"USD", "GBP", "CHF"}) == before  # idempotent


def test_the_next_run_fills_from_the_last_stored_day(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider({"USD": daily(date(2026, 3, 2), date(2026, 3, 3), "1.1")})
    update_fx(db_engine, [provider], date(2026, 3, 3))  # GBP and CHF: no data yet
    provider.series["USD"].update(daily(date(2026, 3, 4), TODAY, "1.2"))
    provider.calls.clear()

    result = update_fx(db_engine, [provider], TODAY)

    assert (("USD",), date(2026, 3, 2), TODAY) in provider.calls  # both stored days, the gap
    assert (("GBP",), date(2006, 3, 10), TODAY) in provider.calls  # still never stored
    assert result.stored == {"USD": 7}  # 2-3 March again, then 4-10 March (weekdays)
    with db_transaction(db_engine, write=False) as session:
        assert fx.last_stored_day(session, "USD") == TODAY
        assert fx.rates_for(session, {"USD"})["USD"][date(2026, 3, 3)] == Decimal("1.1")


def test_a_failing_currency_stores_nothing_and_the_others_continue(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider(
        {"GBP": daily(date(2026, 3, 2), TODAY, "0.85")},
        fail={"USD": ProviderError(ErrorCode.NETWORK, "TimeoutError")},
    )
    result = update_fx(db_engine, [provider], TODAY)

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
    update_fx(db_engine, [provider], TODAY)
    assert checked == ["ok", "ok", "ok"]


def test_a_corrected_or_intraday_value_is_replaced_by_the_next_run(db_engine: Engine) -> None:
    seed(db_engine)
    provider = FakeFxProvider({"USD": {date(2026, 3, 9): Decimal("1.10")}})
    update_fx(db_engine, [provider], date(2026, 3, 9))
    provider.series["USD"][date(2026, 3, 9)] = Decimal("1.12")  # the final value
    update_fx(db_engine, [provider], TODAY)
    with db_transaction(db_engine, write=False) as session:
        assert fx.rates_for(session, {"USD"})["USD"] == {date(2026, 3, 9): Decimal("1.12")}


def test_a_currency_the_first_engine_lacks_comes_from_the_next(db_engine: Engine) -> None:
    seed(db_engine)
    first = FakeFxProvider(
        {"USD": daily(date(2026, 3, 2), TODAY, "1.1")},
        fail={"GBP": ProviderError(ErrorCode.NETWORK, "TimeoutError")},
        name="first",
    )
    second = FakeFxProvider(
        {
            "USD": daily(date(2026, 3, 2), TODAY, "9.9"),
            "GBP": daily(date(2026, 3, 2), TODAY, "0.85"),
            "CHF": daily(date(2026, 3, 2), TODAY, "0.95"),
        },
        name="second",
        capabilities=replace(FX_CAPABILITIES, history_years=1),
    )
    result = update_fx(db_engine, [first, second], TODAY)

    assert result.failed == {}  # the second engine delivered GBP
    assert result.stored == {"USD": 7, "GBP": 7, "CHF": 7}
    assert [c[0] for c in second.calls] == [("CHF",), ("GBP",)]  # USD came from the first
    assert second.calls[0][1] == date(2025, 3, 10)  # its own, shorter history
    with db_transaction(db_engine, write=False) as session:
        assert fx.rates_for(session, {"USD"})["USD"][TODAY] == Decimal("1.1")
        sources = session.execute(
            select(models.FxRate.currency, models.FxRate.source).distinct()
        ).all()
    assert sorted(sources) == [("CHF", "second"), ("GBP", "second"), ("USD", "first")]


def test_a_currency_no_engine_delivers_reports_the_first_error(db_engine: Engine) -> None:
    seed(db_engine)
    first = FakeFxProvider(fail={"USD": ProviderError(ErrorCode.RATE_LIMITED, "429")})
    second = FakeFxProvider(fail={"USD": ProviderError(ErrorCode.NETWORK, "TimeoutError")})
    result = update_fx(db_engine, [first, second], TODAY)
    assert result.failed == {"USD": "RATE_LIMITED"}
    assert result.stored == {}


def test_no_fx_engine_fetches_nothing(db_engine: Engine) -> None:
    seed(db_engine)
    assert update_fx(db_engine, [], TODAY).stored == {}
