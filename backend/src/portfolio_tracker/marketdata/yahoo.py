"""Yahoo Finance price adapter. **Unofficial**: Yahoo publishes no API, offers no terms for
this use and may change or block it at any time (D-118). The app never depends on it: on any
error the last stored closes stay in use with the stale notice, and manual prices work.

Shapes verified against public responses in October 2026 (no documentation exists):

- Search: `GET https://query2.finance.yahoo.com/v1/finance/search?q=…` returns
  `{"quotes": [{"symbol", "exchange", "exchDisp", "quoteType", "shortname", "longname",
  …}]}`. `quoteType` values seen: `EQUITY`, `ETF`. Quotes carry **no currency**.
- Chart: `GET /v8/finance/chart/{symbol}?interval=1d&period1=…&period2=…` returns
  `{"chart": {"result": [{"meta": {"currency", "exchangeName", "instrumentType",
  "exchangeTimezoneName", "gmtoffset", …}, "timestamp": [epoch seconds],
  "indicators": {"quote": [{"close": [number|null], …}], "adjclose": […]}}],
  "error": null}}`. London listings report `"currency": "GBp"` (pence). Timestamps fall at
  the exchange's opening time. An unknown symbol is HTTP 404.

Choices:
- The currency of a search result is read from a short chart call per candidate (at most
  `MAX_CANDIDATES`), because search results do not carry it.
- Daily closes use the raw `close`, never `adjclose` (adjusted for dividends); Yahoo's
  `close` is still adjusted for later splits (D-126).
- Closes are dated in the exchange's time zone.
- Yahoo serialises many closes as float32 values widened to doubles (`98.73999786376953`
  for 98.74). A close that is exactly a float32 value is read as the shortest decimal with
  the same float32 encoding; any other close is kept exactly as received.
- A listing never claims `PERCENT_OF_NOMINAL`: how Yahoo quotes bonds is not verified, so a
  bond asset gets a unit mismatch and keeps its manual prices (D-118, D-119).
"""

import re
import struct
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.marketdata.base import (
    Close,
    CloseSeries,
    ErrorCode,
    Listing,
    ProviderError,
)
from portfolio_tracker.marketdata.http import Transport, check_status, parse_json
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy
from portfolio_tracker.marketdata.units import normalize_currency

NAME = "yahoo"
BASE_URL = "https://query2.finance.yahoo.com"
MAX_CANDIDATES = 6
_QUERY = re.compile(r"[A-Za-z0-9 .\-^=&]{1,40}")
_SYMBOL = re.compile(r"[A-Za-z0-9.\-^=_]{1,32}")
_TYPES = {"EQUITY": AssetType.STOCK, "ETF": AssetType.ETF, "BOND": AssetType.BOND}
_SEARCHABLE = frozenset({"EQUITY", "ETF", "BOND", "MUTUALFUND"})


def _changed(detail: str) -> ProviderError:
    return ProviderError(ErrorCode.FORMAT_CHANGED, detail)


def _float32(value: float) -> float | None:
    try:
        return struct.unpack("f", struct.pack("f", value))[0]
    except OverflowError:
        return None


def from_wire(value: Decimal) -> Decimal:
    """Undo float32 widening: `98.73999786376953` → `98.74`; other values unchanged."""
    as_float = float(value)
    single = _float32(as_float)
    if single is None or single != as_float:
        return value
    for digits in range(1, 10):
        candidate = Decimal(f"{as_float:.{digits}g}")
        if _float32(float(candidate)) == single:
            return Decimal(format(candidate, "f"))  # plain digits, never an exponent
    return value


def _number(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise _changed("a close is not a number")
    number = Decimal(value)
    if not number.is_finite() or number <= 0:
        raise _changed("a close is not positive")
    return from_wire(number)


def _epoch(day: date) -> str:
    """Midnight UTC of `day`, in epoch seconds."""
    return str(int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()))


def _zone(meta: dict[str, Any]) -> tzinfo:
    name = meta.get("exchangeTimezoneName")
    if isinstance(name, str):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    offset = meta.get("gmtoffset")
    if isinstance(offset, int) and not isinstance(offset, bool) and abs(offset) < 86_400:
        return timezone(timedelta(seconds=offset))
    return UTC


def _chart_result(body: object) -> dict[str, Any]:
    chart = body.get("chart") if isinstance(body, dict) else None
    if not isinstance(chart, dict):
        raise _changed("no chart object")
    error = chart.get("error")
    if error:
        if isinstance(error, dict) and error.get("code") == "Not Found":
            raise ProviderError(ErrorCode.NOT_FOUND, "symbol not found")
        raise _changed("chart error")
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise _changed("no chart result")
    result = results[0]
    if not isinstance(result.get("meta"), dict):
        raise _changed("no chart meta")
    return result


class YahooPrices:
    name = NAME

    def __init__(
        self,
        transport: Transport,
        throttle: Throttle | None = None,
        base_url: str = BASE_URL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._transport = transport
        self._throttle = throttle or Throttle(ThrottlePolicy(min_interval=1.0))
        self._base = base_url.rstrip("/")
        self._now = now

    @property
    def throttle(self) -> Throttle:
        return self._throttle

    def _get(self, path: str, params: dict[str, str], max_wait: float | None) -> object:
        def call() -> object:
            response = self._transport.fetch(self._base + path, params)
            check_status(response, self._now())
            return parse_json(response.body)

        return self._throttle.call(call, max_wait=max_wait)

    def _chart(
        self, symbol: str, params: dict[str, str], max_wait: float | None = None
    ) -> dict[str, Any]:
        if not _SYMBOL.fullmatch(symbol):
            raise ProviderError(ErrorCode.NOT_FOUND, "not a symbol")
        path = f"/v8/finance/chart/{quote(symbol, safe='')}"
        return _chart_result(self._get(path, {"interval": "1d", **params}, max_wait))

    # --- search -----------------------------------------------------------------------

    def search(self, query: str, *, max_wait: float | None = None) -> Sequence[Listing]:
        query = query.strip()
        if not _QUERY.fullmatch(query):
            return []
        params = {
            "q": query,
            "quotesCount": "10",
            "newsCount": "0",
            "listsCount": "0",
            "enableFuzzyQuery": "false",
        }
        body = self._get("/v1/finance/search", params, max_wait)
        quotes = body.get("quotes") if isinstance(body, dict) else None
        if not isinstance(quotes, list):
            raise _changed("search without a quotes list")
        listings: list[Listing] = []
        for item in quotes:
            if len(listings) >= MAX_CANDIDATES:
                break
            if not isinstance(item, dict):
                raise _changed("a search quote is not an object")
            symbol, kind = item.get("symbol"), item.get("quoteType")
            if not isinstance(symbol, str) or kind not in _SEARCHABLE:
                continue
            try:
                meta = self._chart(symbol, {"range": "5d"}, max_wait)["meta"]
            except ProviderError as error:
                if error.code is ErrorCode.NOT_FOUND:
                    continue
                raise
            normalized = normalize_currency(meta.get("currency"))
            if normalized is None:
                continue  # an unknown currency code is never guessed (D-119)
            currency, unit = normalized
            name = item.get("longname") or item.get("shortname")
            exchange = item.get("exchDisp") or item.get("exchange")
            listings.append(
                Listing(
                    provider=NAME,
                    symbol=symbol,
                    currency=currency,
                    unit=unit,
                    name=name if isinstance(name, str) else None,
                    exchange=exchange if isinstance(exchange, str) else None,
                    asset_type=_TYPES.get(kind),
                )
            )
        return listings

    # --- daily closes -----------------------------------------------------------------

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1))}
        result = self._chart(symbol, params)
        meta = result["meta"]
        normalized = normalize_currency(meta.get("currency"))
        if normalized is None:
            raise _changed("unknown currency code")
        currency, unit = normalized
        timestamps = result.get("timestamp") or []
        quotes = (result.get("indicators") or {}).get("quote") or [{}]
        if not isinstance(timestamps, list) or not isinstance(quotes, list) or not quotes:
            raise _changed("unexpected chart arrays")
        closes = quotes[0].get("close") if isinstance(quotes[0], dict) else None
        closes = closes or []
        if not isinstance(closes, list) or len(closes) != len(timestamps):
            raise _changed("timestamps and closes differ in length")
        zone = _zone(meta)
        by_day: dict[date, Decimal] = {}
        for stamp, value in zip(timestamps, closes, strict=True):
            if value is None:
                continue  # a day without a close (e.g. a holiday row)
            if isinstance(stamp, bool) or not isinstance(stamp, int):
                raise _changed("a timestamp is not an integer")
            day = datetime.fromtimestamp(stamp, zone).date()
            if start <= day <= end:
                by_day[day] = _number(value)
        return CloseSeries(currency, unit, tuple(Close(day, by_day[day]) for day in sorted(by_day)))
