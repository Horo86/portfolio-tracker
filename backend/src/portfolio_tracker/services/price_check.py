"""The live price check (D-178, D-179): which price source, exchange and latest price an ISIN
would get, asked before the security is saved.

A **stateless replay** of the walk a new instrument gets in `services/market_data.py`: the same
functions (`listing_choice.classify`, `orders`, `search`, `judge`; D-125, D-133, D-136, D-152,
D-172) on the engines of the stored configuration, and nothing else. No instrument, listing,
check or audit row is read or written, so neither the answer nor its timing tells whether
anyone holds the ISIN (D-116, D-125). The answer is therefore *expected*: the listing is chosen
by the linking job after the security is saved.

Where the run would postpone the instrument (a classifier carrying the ISIN back failed, or
no class while a classifier failed), and on the first engine error, the check is unavailable
instead of guessing. The price is the last close of the accepted candidate, read by the
engine's own candidate check (`Listing.last_close`, no extra call), in the asset's quote unit
as a stored close would be read (`units.to_stored`, then `units.to_asset_unit`).

Limits (D-179): every provider call waits for its engine's shared throttle at most
`max_wait` and never past the check's `budget`; once the budget is spent, no further call is
made. At most one check per user runs at a time (D-197, `RunningChecks`). Logs carry the
engine and a code only, never the ISIN, a symbol or a price.
"""

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import Listing, SearchQuery
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.marketdata.throttle import Stopped
from portfolio_tracker.marketdata.units import Mismatch, compatible, to_asset_unit, to_stored
from portfolio_tracker.services.asset_search import SEARCH_MAX_WAIT
from portfolio_tracker.services.listing_choice import Outcome, classify, judge, orders, search

log = logging.getLogger(__name__)

BUDGET_SECONDS = 20.0  # one check, all its provider calls (D-179)
CHECK_LIMIT = 6  # checks per user per minute
AT_ONCE = 2  # checks running at once on the instance
BUSY_RETRY_AFTER = 5  # seconds, when both slots are taken
RUNNING_RETRY_AFTER = 5  # seconds, when the user's own check is still running (D-197)
UNAVAILABLE = "MARKET_DATA_UNAVAILABLE"


class RunningChecks:
    """The users with a price check running (D-197): at most one each, so one user can never
    hold both instance slots. Thread-safe: request handlers run in threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._users: set[UUID] = set()

    def start(self, user_id: UUID) -> bool:
        """Mark the user's check as running; False when one is running already."""
        with self._lock:
            if user_id in self._users:
                return False
            self._users.add(user_id)
            return True

    def finish(self, user_id: UUID) -> None:
        with self._lock:
            self._users.discard(user_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._users)


class CheckOutcome(StrEnum):
    FOUND = "FOUND"
    NO_SOURCE = "NO_SOURCE"  # reason NO_ENGINE or NOT_FOUND
    MISMATCH = "MISMATCH"  # reason CURRENCY or UNIT


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The expected answer. For `FOUND`, `price` (None when the engine showed no close) is in
    `currency` and `quote_unit`, the asset's; `exchange` is the engine's display name."""

    outcome: CheckOutcome
    reason: str | None = None
    source: str | None = None
    exchange: str | None = None
    exchange_code: str | None = None
    symbol: str | None = None
    price: Decimal | None = None
    price_date: date | None = None
    currency: str | None = None
    quote_unit: QuoteUnit | None = None


class CheckUnavailable(Exception):
    """The providers cannot give the answer now: `code` is `MARKET_DATA_UNAVAILABLE`."""

    def __init__(self, code: str = UNAVAILABLE) -> None:
        super().__init__(code)
        self.code = code


def _no_source(reason: str) -> CheckResult:
    return CheckResult(CheckOutcome.NO_SOURCE, reason)


def _stop(provider: str | None, code: str) -> CheckUnavailable:
    log.warning("price check unavailable", extra={"provider": provider, "code": code})
    return CheckUnavailable()


def check(
    engines: BuiltEngines,
    exchanges: Sequence[str],
    *,
    isin: str,
    currency: str,
    unit: QuoteUnit,
    asset_type: AssetType,
    now: datetime,
    clock: Callable[[], float] = time.monotonic,
    budget: float = BUDGET_SECONDS,
    max_wait: float = SEARCH_MAX_WAIT,
) -> CheckResult:
    """The listing a new instrument of (`isin`, `currency`) would get, and its last close in
    the asset's `unit`. Raises `CheckUnavailable` where the run would wait or an engine failed."""
    deadline = clock() + budget

    def wait(provider: str | None) -> float:
        remaining = deadline - clock()
        if remaining <= 0:
            raise _stop(provider, "BUDGET")
        return min(max_wait, remaining)

    if not orders(engines, None):
        return _no_source("NO_ENGINE")  # nothing to ask: no classifier call either
    try:
        errors: Counter[str] = Counter()
        facts = classify(engines, isin, errors, max_wait=wait(None))
        if facts.hints_lost or (facts.price_class is None and facts.failed):
            # The run would postpone: without the hints, or without a class, it would guess.
            raise _stop(None, next(iter(errors), "CLASSIFIER"))
        query = SearchQuery.by_isin(isin, facts.hints, exchanges, currency)
        answers: dict[str, Sequence[Listing]] = {}
        judged: dict[str, Outcome] = {}
        for order_class, provider in orders(engines, facts.price_class):
            if provider.name not in answers:
                answer = search(provider, query, max_wait=wait(provider.name))
                if isinstance(answer, str):
                    raise _stop(provider.name, answer)
                answers[provider.name] = answer
            outcome = judge(provider, answers[provider.name], query, currency, order_class, now)
            judged[provider.name] = outcome
            if outcome.status == "OK" and outcome.listing is not None:
                return _found(outcome.listing, currency, unit, asset_type)
    except Stopped:
        raise _stop(None, "STOPPED") from None
    if not judged:
        return _no_source("NO_ENGINE")  # a class whose order is empty (e.g. a bond)
    mismatch = next((o.mismatch for o in judged.values() if o.status == "MISMATCH"), None)
    if mismatch is not None:
        return CheckResult(CheckOutcome.MISMATCH, mismatch.value)
    return _no_source("NOT_FOUND")


def _found(listing: Listing, currency: str, unit: QuoteUnit, asset_type: AssetType) -> CheckResult:
    rule = compatible(currency, unit, asset_type, listing.currency, listing.unit)
    if isinstance(rule, Mismatch):
        return CheckResult(CheckOutcome.MISMATCH, rule.kind.value)
    price = None
    if listing.last_close is not None:
        price = to_asset_unit(to_stored(listing.last_close, listing.unit), rule)
    return CheckResult(
        CheckOutcome.FOUND,
        None,
        listing.provider,
        listing.exchange or listing.exchange_code,
        listing.exchange_code,
        listing.symbol,
        price,
        listing.last_close_day if price is not None else None,
        currency,
        unit,
    )
