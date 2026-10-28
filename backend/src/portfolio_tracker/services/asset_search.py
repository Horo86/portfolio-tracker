"""Asset search (D-128, D-131): the provider is asked live; nothing stored is read.

A query that is a valid ISIN is searched as an ISIN, anything else as a text (a ticker or a
name), by the first enabled engine of the `ETF_STOCK` order, then of the `BOND` order, that
declares that kind of search. The answer is the provider's results only: no instrument, no
listing and nothing about other users is read, so a result never tells whether anyone holds
the security (D-116). An ISIN result carries the ISIN asked for; a text result has none
(providers do not give it), so it can become a manually priced asset, or be searched again by
ISIN. Adding an asset passes only the ISIN and the quote currency: the listing that feeds it
is always chosen by the linking job (D-125, D-136), never by the user.

Traffic (D-128): the same throttle as the background jobs, waited on for at most
`SEARCH_MAX_WAIT` seconds, and at most `SEARCH_LIMIT` searches per user in `SEARCH_WINDOW`.
The typed text is never logged: logs carry the engine and an error code only.
"""

import logging
import math
import threading
from collections import OrderedDict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import (
    ErrorCode,
    Listing,
    PriceClass,
    PriceProvider,
    ProviderError,
    SearchQuery,
)
from portfolio_tracker.marketdata.registry import BuiltEngines
from portfolio_tracker.textformats import valid_isin

log = logging.getLogger(__name__)

SEARCH_LIMIT = 20
SEARCH_WINDOW = timedelta(minutes=1)
SEARCH_MAX_WAIT = 10.0  # seconds a search may wait for its engine's throttle
MAX_USERS = 10_000  # bounds the limiter's memory


@dataclass(frozen=True, slots=True)
class SearchResult:
    name: str | None
    symbol: str  # the provider's, for display only: never sent back to create an asset
    exchange: str | None
    exchange_code: str | None  # the engine's code of that exchange, for the page to name it
    currency: str
    quote_unit: QuoteUnit
    asset_type: AssetType | None  # the provider's proposal
    isin: str | None  # the ISIN asked for; None for a text search


class SearchUnavailable(Exception):
    """The engine could not answer: `code` is `MARKET_DATA_BUSY` (with `retry_after`) or
    `MARKET_DATA_UNAVAILABLE`."""

    def __init__(self, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


class SearchLimiter:
    """At most `limit` searches per user in a sliding `window`, in memory (one process,
    ADR-0006). `take` returns None when allowed, else the seconds to wait."""

    def __init__(self, limit: int = SEARCH_LIMIT, window: timedelta = SEARCH_WINDOW) -> None:
        self.limit, self.window = limit, window
        self._lock = threading.Lock()
        self._seen: OrderedDict[UUID, deque[datetime]] = OrderedDict()

    def take(self, user_id: UUID, now: datetime) -> int | None:
        with self._lock:
            times = self._seen.pop(user_id, deque())
            while times and times[0] <= now - self.window:
                times.popleft()
            refused = len(times) >= self.limit
            if not refused:
                times.append(now)
            self._seen[user_id] = times  # most recent last
            while len(self._seen) > MAX_USERS:
                self._seen.popitem(last=False)
            if not refused:
                return None
            return max(1, math.ceil((times[0] + self.window - now).total_seconds()))


def _searchers(engines: BuiltEngines) -> list[PriceProvider]:
    seen: dict[str, PriceProvider] = {}
    for price_class in (PriceClass.ETF_STOCK, PriceClass.BOND):
        for provider in engines.prices(price_class):
            seen.setdefault(provider.name, provider)
    return list(seen.values())


def _result(listing: Listing, isin: str | None) -> SearchResult:
    return SearchResult(
        listing.name,
        listing.symbol,
        listing.exchange or listing.exchange_code,
        listing.exchange_code,
        listing.currency,
        listing.unit,
        listing.asset_type,
        isin,
    )


def search(engines: BuiltEngines, exchanges: Sequence[str], text: str) -> list[SearchResult]:
    """The provider's results for `text`. Raises `SearchUnavailable` when no enabled engine
    can search that kind of query, or the engine fails."""
    candidate = text.strip().upper()
    isin = candidate if valid_isin(candidate) else None
    for provider in _searchers(engines):
        capabilities = provider.capabilities
        if not (capabilities.search_isin if isin else capabilities.search_text):
            continue
        if isin:
            query = SearchQuery.by_isin(isin, (), exchanges)
        else:
            query = SearchQuery.by_text(text.strip(), exchanges)
        try:
            found = provider.search(query, max_wait=SEARCH_MAX_WAIT)
        except ProviderError as error:
            log.warning(
                "asset search failed", extra={"provider": provider.name, "code": error.code}
            )
            if error.code is ErrorCode.RATE_LIMITED and error.retry_after is not None:
                # Busy for a known time. A throttle paused after repeated failures (until the
                # next run) or a 429 without Retry-After gives no time: unavailable, no hint.
                wait = math.ceil(error.retry_after)
                raise SearchUnavailable("MARKET_DATA_BUSY", max(1, wait)) from None
            raise SearchUnavailable("MARKET_DATA_UNAVAILABLE") from None
        except Exception as error:  # an adapter bug: its message could hold the text
            log.warning(
                "asset search failed",
                extra={"provider": provider.name, "code": ErrorCode.ADAPTER_ERROR,
                       "type": type(error).__name__},
            )  # fmt: skip
            raise SearchUnavailable("MARKET_DATA_UNAVAILABLE") from None
        return [_result(listing, isin) for listing in found]
    raise SearchUnavailable("MARKET_DATA_UNAVAILABLE")
