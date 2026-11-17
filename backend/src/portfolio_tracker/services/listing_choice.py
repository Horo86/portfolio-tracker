"""The choice of an instrument's listing among an engine's candidates, and the classifiers'
answer for an ISIN (D-125, D-133, D-136, D-152, D-172).

Moved unchanged from `services/market_data.py`, which walks every linked instrument with
these functions. Nothing here reads or writes the database: what is judged is only the
engines' answers, the instrument's currency and its class. Logs carry engine names and error
codes, never an ISIN.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from portfolio_tracker.marketdata.acceptance import (
    Rejection,
    confirmed_tickers,
    contradicts_confirmed,
    rejection,
)
from portfolio_tracker.marketdata.base import (
    ErrorCode,
    Identifier,
    Listing,
    PriceClass,
    PriceProvider,
    ProviderError,
    SearchQuery,
)
from portfolio_tracker.marketdata.ranking import candidate_rank
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.units import MismatchKind

if TYPE_CHECKING:
    from portfolio_tracker.services.market_data import ResolveResult, _Pending

log = logging.getLogger(__name__)

# Unknown class: the bond order first, then the ETF/stock one (D-133).
UNKNOWN_CLASS_ORDER = (PriceClass.BOND, PriceClass.ETF_STOCK)

MISMATCH = {
    Rejection.CURRENCY: MismatchKind.CURRENCY,
    Rejection.UNIT: MismatchKind.UNIT,
    Rejection.CLASS: MismatchKind.UNIT,
}


@dataclass(frozen=True)
class Outcome:
    """One engine's answer for an instrument."""

    provider: str
    status: str
    listing: Listing | None = None
    mismatch: MismatchKind | None = None
    error: str | None = None


def orders(
    engines: BuiltEngines, price_class: PriceClass | None
) -> list[tuple[PriceClass, PriceProvider]]:
    classes = (price_class,) if price_class else UNKNOWN_CLASS_ORDER
    return [(c, provider) for c in classes for provider in engines.prices(c)]


@dataclass(frozen=True)
class Facts:
    """What the classifiers said about one ISIN."""

    price_class: PriceClass | None = None
    source: str | None = None  # the classifier that gave the class
    hints: tuple[Identifier, ...] = ()
    failed: bool = False  # a classifier failed
    hints_lost: bool = False  # a classifier carrying the ISIN back failed: no hints from it


def classify(engines: BuiltEngines, isin: str, result: "ResolveResult") -> Facts:
    price_class: PriceClass | None = None
    source: str | None = None
    hints: tuple[Identifier, ...] = ()
    failed = hints_lost = False
    for classifier in engines.classifiers():
        try:
            facts = classifier.classify(isin)
        except Exception as error:  # an adapter bug must not stop the run (D-121)
            code = error_code(error)
            failed = True
            hints_lost = hints_lost or classifier.capabilities.echoes_isin
            result.errors[code] += 1
            log.warning("classifier failed", extra={"provider": classifier.name, "code": code})
            continue
        if facts is None:
            continue
        if not hints and classifier.capabilities.echoes_isin:
            hints = facts.identifiers  # only an exact ISIN lookup may anchor a match (D-133)
        if facts.price_class is not None:
            price_class, source = facts.price_class, classifier.name
            break
    return Facts(price_class, source, hints, failed, hints_lost)


def error_code(error: Exception) -> str:
    """A provider error's code; any other exception is an adapter bug, reduced to a code (its
    message could hold an ISIN)."""
    return error.code if isinstance(error, ProviderError) else ErrorCode.ADAPTER_ERROR


def search(provider: PriceProvider, query: SearchQuery) -> Sequence[Listing] | str:
    """The engine's candidates, or the error code of its failure."""
    try:
        return provider.search(query)
    except Exception as error:  # an adapter bug must not stop the run (D-121)
        code = error_code(error)
        log.warning(
            "listing search failed",
            extra={"provider": provider.name, "code": code, "type": type(error).__name__},
        )
        return code


def judge(
    provider: PriceProvider,
    found: Sequence[Listing],
    query: SearchQuery,
    pending: "_Pending",
    price_class: PriceClass,
    now: datetime,
) -> Outcome:
    """The first accepted candidate in rank, or why none is."""
    main = provider.capabilities.main_exchanges
    home = provider.capabilities.home_exchanges(pending.currency)

    def rank(listing: Listing):
        return candidate_rank(
            symbol=listing.symbol,
            exchange_code=listing.exchange_code,
            exchange_name=listing.exchange,
            matched_by=listing.matched_by,
            preferred=query.exchanges,
            main=main,
            home=home,
        )

    reasons: list[Rejection] = []
    confirmed = confirmed_tickers(found)
    for listing in sorted(found, key=rank):  # the engine's order is not trusted (D-125)
        reason = rejection(
            listing,
            currency=pending.currency,
            price_class=price_class,
            declared_units=provider.capabilities.units,
            today=now.date(),
        )
        if reason is None and contradicts_confirmed(listing, confirmed):
            reason = Rejection.TICKER  # another security answered for the ISIN (D-172)
        if reason is None:
            return Outcome(provider.name, "OK", listing=listing)
        reasons.append(reason)
    if reasons and reasons[0] in MISMATCH:
        return Outcome(provider.name, "MISMATCH", mismatch=MISMATCH[reasons[0]])
    return Outcome(provider.name, "NOT_FOUND")
