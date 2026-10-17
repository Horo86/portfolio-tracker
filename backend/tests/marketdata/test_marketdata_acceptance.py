"""Whether a candidate listing may feed an instrument (D-119, D-133, D-136). Invented data."""

from datetime import date

import pytest

from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.acceptance import RECENT_CLOSE_DAYS, Rejection, rejection
from portfolio_tracker.marketdata.base import Listing, PriceClass
from portfolio_tracker.marketdata.yahoo import CAPABILITIES as YAHOO

TODAY = date(2026, 3, 16)
CUR, MINOR, PCT = QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT, QuoteUnit.PERCENT_OF_NOMINAL
ALL_UNITS = frozenset(QuoteUnit)


def listing(currency: str = "EUR", unit: QuoteUnit = CUR, last: date | None = TODAY) -> Listing:
    return Listing("alpha", "XMPW.DE", currency, unit, last_close_day=last)


def check(candidate: Listing, price_class: PriceClass = PriceClass.ETF_STOCK, units=ALL_UNITS):
    return rejection(
        candidate, currency="EUR", price_class=price_class, declared_units=units, today=TODAY
    )


def test_the_threshold_is_ten_days() -> None:
    assert RECENT_CLOSE_DAYS == 10


@pytest.mark.parametrize(
    ("last", "expected"),
    [
        (TODAY, None),
        (date(2026, 3, 6), None),  # exactly 10 days before
        (date(2026, 3, 5), Rejection.NO_RECENT_CLOSE),  # 11 days before
        (date(2025, 12, 1), Rejection.NO_RECENT_CLOSE),
        (None, Rejection.NO_RECENT_CLOSE),  # no close at all in the month read
    ],
)
def test_an_etf_or_stock_needs_a_recent_close(last: date | None, expected) -> None:
    assert check(listing(last=last)) == expected


def test_the_currency_must_be_the_instruments() -> None:
    assert check(listing(currency="USD")) is Rejection.CURRENCY
    assert check(listing(currency="GBP", unit=MINOR)) is Rejection.CURRENCY  # pence of GBP
    assert check(listing(currency="EUR", unit=MINOR)) is None  # a minor unit of EUR fits


def test_currency_is_checked_before_anything_else() -> None:
    assert check(listing(currency="USD", unit=PCT, last=None)) is Rejection.CURRENCY


def test_a_unit_the_engine_does_not_declare_is_refused() -> None:
    assert check(listing(unit=PCT), units=YAHOO.units) is Rejection.UNIT


def test_the_unit_must_fit_the_price_class() -> None:
    assert check(listing(unit=PCT)) is Rejection.CLASS
    assert check(listing(unit=CUR), PriceClass.BOND) is Rejection.CLASS
    assert check(listing(unit=PCT, last=None), PriceClass.BOND) is None  # bonds: no 10-day rule
