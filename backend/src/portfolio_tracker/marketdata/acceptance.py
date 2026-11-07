"""Whether a listing found for an instrument may feed it (D-119, D-133, D-136, D-172).

A pure check, applied to every candidate before it is stored as a listing: by the market
data service when it links instruments (step 9), and by the local coverage run. An engine
may also refuse a candidate before this (Yahoo checks a hint's exchange and type itself).
"""

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from enum import StrEnum

from portfolio_tracker.engine.model import QuoteUnit
from portfolio_tracker.marketdata.base import Listing, MatchSource, PriceClass
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
    TICKER = "TICKER"  # found by ISIN, not the confirmed ticker of its exchange (D-172)


def _base(symbol: str) -> str:
    """A symbol without its exchange suffix, ignoring case: `XMPWM.MI` → `XMPWM`."""
    return symbol.split(".", 1)[0].casefold()


def confirmed_tickers(found: Iterable[Listing]) -> dict[str, set[str]]:
    """Per exchange code, the tickers a classifier carrying the ISIN back confirmed: those of
    the candidates the engine found from its hints (only such a classifier gives hints, D-133).
    """
    confirmed: dict[str, set[str]] = {}
    for listing in found:
        if listing.matched_by is MatchSource.HINT and listing.hint and listing.exchange_code:
            confirmed.setdefault(listing.exchange_code, set()).add(_base(listing.hint.ticker))
    return confirmed


def contradicts_confirmed(listing: Listing, confirmed: Mapping[str, set[str]]) -> bool:
    """A candidate found by the ISIN search on an exchange where a confirmed ticker was found,
    whose symbol is not that ticker: another security answered for the ISIN (D-172). Where no
    confirmed ticker was found, nothing is refused (the confirmed listing must be there)."""
    if listing.matched_by is not MatchSource.ISIN or listing.exchange_code is None:
        return False
    tickers = confirmed.get(listing.exchange_code)
    return bool(tickers) and _base(listing.symbol) not in tickers


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
