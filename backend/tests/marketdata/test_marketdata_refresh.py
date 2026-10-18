"""Closes and switches (D-119, D-122, D-134): back-fill, the daily update from the last stored
days, make before break with the level check, a disabled engine, a replacement within one
engine. Fake engines, a migrated database file, invented ISINs, symbols and closes."""

import logging
import sqlite3
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from engines import (
    ETF_ISIN,
    NOW,
    FakePrices,
    built,
    holdings,
    listing,
)
from sqlalchemy import Engine, select

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import Close, CloseSeries, ErrorCode, ProviderError
from portfolio_tracker.repositories import instruments, prices
from portfolio_tracker.services.fx_rates import horizon_start
from portfolio_tracker.services.market_data import (
    LEVEL_MIN_DAYS,
    RETRY_DAYS,
    refresh_prices,
)

TODAY = NOW.date()


def series(currency: str = "EUR", unit: QuoteUnit = QuoteUnit.CURRENCY, **by_day: str):
    """`series(d0302="9.80")`: closes on 2 March 2026 and so on."""
    closes = tuple(
        Close(date(2026, int(k[1:3]), int(k[3:5])), Decimal(v)) for k, v in sorted(by_day.items())
    )
    return CloseSeries(currency, unit, closes)


WEEK = {"d0302": "10.00", "d0303": "10.10", "d0304": "10.20", "d0305": "10.30"}


def alpha_with(symbol: str = "XMPW.MI", code: str = "MIL", **closes: str) -> FakePrices:
    alpha = FakePrices("alpha", {ETF_ISIN: [listing(symbol, code)]})
    alpha.closes[symbol] = series(**(closes or WEEK))
    return alpha


def run(engine: Engine, engines, when=NOW, exchanges=(), recheck=False):
    return refresh_prices(engine, engines, exchanges, when, recheck=recheck)


def asset_closes(engine: Engine, owner: str = "alice", currency: str = "EUR") -> dict[date, str]:
    with db_transaction(engine, write=False) as session:
        user = session.scalars(select(models.User).where(models.User.username == owner)).one()
        asset = session.scalars(
            select(models.Asset).where(
                models.Asset.owner_id == user.id, models.Asset.quote_currency == currency
            )
        ).one()
        found = prices.closes_for(session, user.id, {asset.id})[asset.id]
    return {day: str(close) for day, close in sorted(found.items())}


def listings_of(engine: Engine) -> dict[str, models.InstrumentListing]:
    with db_transaction(engine, write=False) as session:
        instrument = instruments.get_instrument(session, ETF_ISIN, "EUR")
        return {f"{x.provider}:{x.symbol}": x for x in instruments.listings(session, instrument.id)}


def check_of(engine: Engine, provider: str) -> models.InstrumentEngineCheck:
    with db_transaction(engine, write=False) as session:
        instrument = instruments.get_instrument(session, ETF_ISIN, "EUR")
        return session.get(models.InstrumentEngineCheck, (instrument.id, provider))


# --- back-fill and the daily update (D-122) ---------------------------------------------------


def test_a_new_listing_is_back_filled_from_the_horizon_in_the_same_run(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"), ("bob", ETF_ISIN, "EUR"))
    alpha = alpha_with()

    resolved, closes, _ = run(db_engine, built([alpha]))

    assert resolved.chosen == 1 and (closes.updated, closes.stored) == (1, 4)
    assert alpha.fetches == [("XMPW.MI", horizon_start(TODAY), TODAY)]  # one for both users
    expected = {date(2026, 3, d): v for d, v in zip(range(2, 6), WEEK.values(), strict=True)}
    assert asset_closes(db_engine) == {d: v for d, v in expected.items()}
    assert asset_closes(db_engine, "bob") == asset_closes(db_engine)
    assert listings_of(db_engine)["alpha:XMPW.MI"].last_success_at == NOW


def test_later_runs_fetch_from_the_last_stored_days_and_fill_gaps(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = alpha_with()
    run(db_engine, built([alpha]))
    alpha.closes["XMPW.MI"] = series(d0304="10.25", d0605="11.00")  # a correction, months later
    later = NOW + timedelta(days=91)

    run(db_engine, built([alpha]), later)

    assert alpha.fetches[1] == ("XMPW.MI", date(2026, 3, 1), later.date())  # last 5 days again
    assert len(alpha.queries) == 1  # no new search for a listed instrument
    found = asset_closes(db_engine)
    assert (found[date(2026, 3, 4)], found[date(2026, 6, 5)]) == ("10.25", "11.00")


def test_a_rerun_is_idempotent(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = alpha_with()
    run(db_engine, built([alpha]))
    first = asset_closes(db_engine)
    resolved, closes, switched = run(db_engine, built([alpha]))
    assert asset_closes(db_engine) == first and closes.updated == 1
    assert (resolved.chosen, resolved.targets, switched.switched) == (0, [], 0)


def test_pence_are_stored_in_pounds_and_keep_their_unit(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "GBP"))
    alpha = FakePrices(
        "alpha",
        {ETF_ISIN: [listing("XMPW.L", "LSE", currency="GBP", unit=QuoteUnit.MINOR_CURRENCY_UNIT)]},
    )
    alpha.closes["XMPW.L"] = series("GBP", QuoteUnit.MINOR_CURRENCY_UNIT, d0305="1234.5")
    run(db_engine, built([alpha]))
    with db_transaction(db_engine, write=False) as session:
        (close,) = session.scalars(select(models.InstrumentPrice)).all()
    assert (close.close, close.meta) == (Decimal("12.345"), {"unit": "MINOR_CURRENCY_UNIT"})
    assert asset_closes(db_engine, currency="GBP") == {date(2026, 3, 5): "12.345"}


def test_an_answer_in_another_unit_stores_nothing_and_clears_itself(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = alpha_with()
    alpha.closes["XMPW.MI"] = series("EUR", QuoteUnit.MINOR_CURRENCY_UNIT, d0305="1030")

    _, closes, _ = run(db_engine, built([alpha]))

    row = listings_of(db_engine)["alpha:XMPW.MI"]
    assert (closes.mismatch, row.status, row.mismatch) == (1, "MISMATCH", "UNIT")
    assert asset_closes(db_engine) == {}
    alpha.closes["XMPW.MI"] = series(**WEEK)
    run(db_engine, built([alpha]))
    row = listings_of(db_engine)["alpha:XMPW.MI"]
    assert (row.status, row.mismatch, len(asset_closes(db_engine))) == ("OK", None, 4)


def test_a_failure_keeps_the_stored_closes(db_engine: Engine) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    alpha = alpha_with()
    run(db_engine, built([alpha]))
    alpha.closes["XMPW.MI"] = ProviderError(ErrorCode.RATE_LIMITED, "paused")

    _, closes, _ = run(db_engine, built([alpha]), NOW + timedelta(days=1))

    row = listings_of(db_engine)["alpha:XMPW.MI"]
    assert closes.errors == {"RATE_LIMITED": 1} and len(asset_closes(db_engine)) == 4
    assert (row.status, row.failure_count, row.last_error_code, row.is_active) == (
        "FAILING",
        1,
        "RATE_LIMITED",
        True,
    )


# --- make before break (D-134) ----------------------------------------------------------------


def beta_with(symbol: str = "XMPW.DE", code: str = "GER", **closes: str) -> FakePrices:
    beta = FakePrices("beta", {ETF_ISIN: [listing(symbol, code, provider="beta")]})
    beta.closes[symbol] = series(**(closes or WEEK))
    return beta


def linked_to_alpha(engine: Engine) -> FakePrices:
    holdings(engine, ("alice", ETF_ISIN, "EUR"))
    alpha = alpha_with()
    run(engine, built([alpha]))
    return alpha


def test_a_better_engine_is_back_filled_then_switched_in_one_run(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    beta = beta_with(d0303="10.11", d0304="10.19", d0305="10.31")  # the same level
    later = NOW + timedelta(days=1)

    resolved, _, switched = run(db_engine, built([beta, alpha]), later)

    assert len(resolved.targets) == 1 and switched.switched == 1
    rows = listings_of(db_engine)
    assert rows["beta:XMPW.DE"].is_active and rows["beta:XMPW.DE"].activated_at == later
    assert not rows["alpha:XMPW.MI"].is_active  # another engine's listing is kept
    # never stitched: the asset reads only the new listing's closes
    assert asset_closes(db_engine) == {
        date(2026, 3, 3): "10.11",
        date(2026, 3, 4): "10.19",
        date(2026, 3, 5): "10.31",
    }


def test_until_the_target_is_back_filled_the_active_listing_stays(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    beta = beta_with()
    beta.closes["XMPW.DE"] = ProviderError(ErrorCode.NETWORK, "timeout")

    _, _, switched = run(db_engine, built([beta, alpha]), NOW + timedelta(days=1))

    assert (switched.switched, switched.waiting) == (0, 1)
    assert listings_of(db_engine)["alpha:XMPW.MI"].is_active
    assert len(asset_closes(db_engine)) == 4  # no window without prices
    beta.closes["XMPW.DE"] = series(**WEEK)
    _, _, switched = run(db_engine, built([beta, alpha]), NOW + timedelta(days=2))
    assert switched.switched == 1 and len(beta.queries) == 1  # offered again, not searched


def test_the_level_check_refuses_a_listing_at_another_level(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    beta = beta_with(d0303="20.20", d0304="20.40", d0305="20.60")  # twice the level
    engines = built([beta, alpha])

    _, _, switched = run(db_engine, engines, NOW + timedelta(days=1))

    assert (switched.switched, switched.level_refused) == (0, 1)
    rows = listings_of(db_engine)
    assert rows["alpha:XMPW.MI"].is_active
    assert (rows["beta:XMPW.DE"].status, rows["beta:XMPW.DE"].mismatch) == ("MISMATCH", "LEVEL")
    assert (check_of(db_engine, "beta").status, check_of(db_engine, "beta").mismatch) == (
        "MISMATCH",
        "LEVEL",
    )

    soon = run(db_engine, engines, NOW + timedelta(days=RETRY_DAYS))
    assert soon[0].targets == [] and len(beta.queries) == 1  # waits 30 days
    run(db_engine, engines, NOW + timedelta(days=RETRY_DAYS + 1))
    assert len(beta.queries) == 2  # searched again, checked again


def test_with_too_few_common_days_the_switch_happens(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    assert LEVEL_MIN_DAYS == 3
    beta = beta_with(d0304="20.40", d0305="20.60")  # twice the level, on two common days only
    _, _, switched = run(db_engine, built([beta, alpha]), NOW + timedelta(days=1))
    assert switched.switched == 1


def test_a_disabled_engine_is_replaced_without_level_check_and_not_called(
    db_engine: Engine,
) -> None:
    """The administrator disabled alpha: it is not asked any more, its listing stays active
    until beta is back-filled, and beta's level is not compared with it."""
    alpha = linked_to_alpha(db_engine)
    beta = beta_with(d0303="20.20", d0304="20.40", d0305="20.60")

    _, _, switched = run(db_engine, built([beta]), NOW + timedelta(days=1))

    assert switched.switched == 1 and len(alpha.fetches) == 1  # only the first back-fill
    assert listings_of(db_engine)["beta:XMPW.DE"].is_active


def test_a_new_exchange_order_replaces_a_listing_within_one_engine(db_engine: Engine) -> None:
    """The re-check (after the administrator changed the exchanges) searches the engine again;
    the new listing is back-filled, then activated while the old listing of the same engine
    and its closes are deleted, in one transaction."""
    alpha = linked_to_alpha(db_engine)
    alpha.answers[ETF_ISIN] = [listing("XMPW.MI", "MIL"), listing("XMPW.DE", "GER")]
    alpha.closes["XMPW.DE"] = series(d0303="10.12", d0304="10.18", d0305="10.29")

    resolved, _, switched = run(
        db_engine, built([alpha]), NOW + timedelta(days=1), exchanges=("GER",), recheck=True
    )

    assert len(resolved.targets) == 1 and switched.switched == 1
    rows = listings_of(db_engine)
    assert list(rows) == ["alpha:XMPW.DE"] and rows["alpha:XMPW.DE"].is_active
    assert check_of(db_engine, "alpha").symbol == "XMPW.DE"
    with db_transaction(db_engine, write=False) as session:
        listing_ids = {row.listing_id for row in session.scalars(select(models.InstrumentPrice))}
    assert listing_ids == {rows["alpha:XMPW.DE"].id}  # the old closes went with their listing


def test_a_failed_re_search_keeps_the_working_listing(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    alpha.answers[ETF_ISIN] = ProviderError(ErrorCode.HTTP_ERROR, "503")
    resolved, _, _ = run(db_engine, built([alpha]), NOW + timedelta(days=1), recheck=True)
    check = check_of(db_engine, "alpha")
    assert resolved.errors == {"HTTP_ERROR": 1} and resolved.targets == []
    assert (check.status, check.symbol, check.failure_count) == ("OK", "XMPW.MI", 1)
    assert listings_of(db_engine)["alpha:XMPW.MI"].is_active


def test_a_symbol_the_provider_dropped_is_searched_again_and_replaced(db_engine: Engine) -> None:
    alpha = linked_to_alpha(db_engine)
    alpha.closes["XMPW.MI"] = ProviderError(ErrorCode.NOT_FOUND, "404")
    run(db_engine, built([alpha]), NOW + timedelta(days=1))
    assert listings_of(db_engine)["alpha:XMPW.MI"].status == "NOT_FOUND"
    assert check_of(db_engine, "alpha").status == "FAILING"
    assert listings_of(db_engine)["alpha:XMPW.MI"].is_active  # until a replacement is ready

    alpha.answers[ETF_ISIN] = [listing("XMPW1.MI", "MIL")]
    alpha.closes["XMPW1.MI"] = series(d0304="10.21", d0305="10.33", d0306="10.40")
    _, _, switched = run(db_engine, built([alpha]), NOW + timedelta(days=2))

    assert len(alpha.queries) == 2 and switched.switched == 1
    assert list(listings_of(db_engine)) == ["alpha:XMPW1.MI"]


# --- network discipline -----------------------------------------------------------------------


def test_no_transaction_is_open_while_closes_are_fetched(db_engine: Engine, tmp_path: Path) -> None:
    holdings(db_engine, ("alice", ETF_ISIN, "EUR"))
    checked: list[str] = []

    def take_the_write_lock_elsewhere() -> None:
        other = sqlite3.connect(tmp_path / "db" / "app.sqlite", timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
        finally:
            other.close()
        assert db_engine.pool.checkedout() == 0
        checked.append("ok")

    class Watched(FakePrices):
        def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
            take_the_write_lock_elsewhere()
            return super().daily_closes(symbol, start, end)

    alpha = Watched("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    alpha.closes["XMPW.MI"] = series(**WEEK)
    run(db_engine, built([alpha]))
    assert checked == ["ok"]


def test_no_isin_reaches_the_logs_when_closes_fail(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    alpha = linked_to_alpha(db_engine)
    alpha.closes["XMPW.MI"] = ProviderError(ErrorCode.NETWORK, "timeout")
    with caplog.at_level(logging.DEBUG):
        run(db_engine, built([alpha]), NOW + timedelta(days=1))
    text = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert "closes fetch failed" in caplog.text and ETF_ISIN not in text
