"""Whether a listing found for an instrument may feed it (D-119, D-133, D-136).

A pure check, applied to every candidate before it is stored as a listing: by the market
data service when it links instruments (step 9), and by the local coverage run. An engine
may also refuse a candidate before this (Yahoo checks a hint's exchange and type itself).
"""

from datetime import date, timedelta
from enum import StrEnum

from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import Listing, PriceClass
from portfolio_tracker.marketdata.units import fits_class

# An ETF/stock candidate must show a close at most this many calendar days before the day
# of the check (D-136): a listing without recent trading is stale or another security.
RECENT_CLOSE_DAYS = 10


class Rejection(StrEnum):
    """Why a candidate is not used, checked in this order."""

    CURRENCY = "CURRENCY"  # not the instrument's quote currency
    UNIT = "UNIT"  # a unit the engine does not declare
    CLASS = "CLASS"  # a unit that does not fit the instrument's price class
    NO_RECENT_CLOSE = "NO_RECENT_CLOSE"  # no close in the last RECENT_CLOSE_DAYS


def rejection(
    listing: Listing,
    *,
    currency: str,
    price_class: PriceClass,
    declared_units: frozenset[QuoteUnit],
    today: date,
) -> Rejection | None:
    """None when `listing` may feed an instrument of `currency` and `price_class`."""
    if listing.currency != currency:
        return Rejection.CURRENCY
    if listing.unit not in declared_units:
        return Rejection.UNIT
    if not fits_class(price_class, listing.unit):
        return Rejection.CLASS
    if price_class is PriceClass.ETF_STOCK:
        last = listing.last_close_day
        if last is None or last < today - timedelta(days=RECENT_CLOSE_DAYS):
            return Rejection.NO_RECENT_CLOSE
    return None
