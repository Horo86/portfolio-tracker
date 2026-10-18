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
- A listing never claims `PERCENT_OF_NOMINAL`: how Yahoo quotes bonds is not verified, so
  Yahoo serves the `ETF_STOCK` role only and a bond keeps its manual prices (D-118, D-119).
- Classification (D-131, D-133): the `quoteType` of the first usable search result for an
  ISIN gives the price class (`EQUITY` and `ETF`: ETF/stock; `BOND`: bond). Search results do
  not carry the ISIN back, so Yahoo's answers never anchor another engine's match.
- Classifier hints (D-136): besides its own results for an ISIN, a search tries one symbol
  per hint, ticker + the suffix of the hint's exchange (`HINT_EXCHANGES`, verified on 17
  October 2026 with public securities: OpenFIGI ticker with that MIC → Yahoo chart). Such a
  candidate is kept only when the chart's `exchangeName` is the one expected for that MIC
  and its `instrumentType` agrees with the hint's security type (`HINT_TYPES`).
- Candidate rank (D-125, `ranking.py`): own results and hint symbols are ranked together
  before any chart call (a symbol found both ways counts as found by ISIN), and only the
  first `MAX_CANDIDATES` are checked; listings come back in that rank. Main exchanges, by
  Yahoo's `exchange` code: the European exchanges of `HINT_EXCHANGES` in table order, then
  the US exchanges `US_EXCHANGES`, which have no symbol suffix (codes seen on 18 October 2026
  with public symbols: IBM `NYQ` NYSE, INTC `NMS` NasdaqGS, JEPQ `NGM` NasdaqGM, PLUG `NCM`
  NasdaqCM, SPY `PCX` NYSEArca, IMO `ASE` NYSE American, ARKK `BTS` Cboe US). Yahoo's search
  by ISIN finds US securities itself (checked with Intel), so no US hint is needed.
- Each candidate's chart covers the last month, so the listing reports the day of its latest
  close (the recent-close rule is applied by `acceptance`, D-136).
"""

import re
import struct
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    Close,
    CloseSeries,
    ErrorCode,
    Identifier,
    Listing,
    MatchSource,
    PriceClass,
    ProviderError,
    Role,
    SearchQuery,
    SecurityFacts,
)
from portfolio_tracker.marketdata.http import Transport, check_status, parse_json
from portfolio_tracker.marketdata.ranking import Rank, candidate_rank
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy
from portfolio_tracker.marketdata.units import normalize_currency

NAME = "yahoo"
BASE_URL = "https://query2.finance.yahoo.com"
MAX_CANDIDATES = 6  # chart calls per search, own results and hints together
# Search results asked of Yahoo, all ranked before the cap (D-125): enough that Yahoo's own
# order rarely decides which listings are seen (25 accepted on 18 October 2026).
MAX_QUOTES = 25
CHECK_RANGE = "1mo"  # chart range read for each candidate: currency and latest close
# Exchanges reached through classifier hints (D-136, ADR-0007 table): ISO 10383 MIC →
# (Yahoo symbol suffix, `exchangeName` in Yahoo's chart answer).
HINT_EXCHANGES = {
    "MTAA": (".MI", "MIL"),
    "XETR": (".DE", "GER"),
    "XLON": (".L", "LSE"),
    "XAMS": (".AS", "AMS"),
    "XPAR": (".PA", "PAR"),
    "XSWX": (".SW", "EBS"),
    "XMAD": (".MC", "MCE"),
    "XBRU": (".BR", "BRU"),
    "XWBO": (".VI", "VIE"),
}
# US exchanges by Yahoo `exchange` code (no symbol suffix), verified with public symbols.
US_EXCHANGES = ("NYQ", "NMS", "NGM", "NCM", "PCX", "ASE", "BTS")
# A hint's security type (OpenFIGI) → the `instrumentType` Yahoo must report; other types
# give no candidate.
HINT_TYPES = {"ETP": "ETF", "Common Stock": "EQUITY"}
_HINT_TICKER = re.compile(r"[A-Z0-9]{1,12}")
_QUERY = re.compile(r"[A-Za-z0-9 .\-^=&]{1,40}")
_SYMBOL = re.compile(r"[A-Za-z0-9.\-^=_]{1,32}")
_TYPES = {"EQUITY": AssetType.STOCK, "ETF": AssetType.ETF, "BOND": AssetType.BOND}
_SEARCHABLE = frozenset({"EQUITY", "ETF", "BOND", "MUTUALFUND"})
_CLASSES = {"EQUITY": PriceClass.ETF_STOCK, "ETF": PriceClass.ETF_STOCK, "BOND": PriceClass.BOND}
CAPABILITIES = Capabilities(
    roles=frozenset({Role.ETF_STOCK, Role.CLASSIFY}),
    search_isin=True,
    search_text=True,
    units=frozenset({QuoteUnit.CURRENCY, QuoteUnit.MINOR_CURRENCY_UNIT}),
    official=False,
    main_exchanges=tuple(code for _, code in HINT_EXCHANGES.values()) + US_EXCHANGES,
)


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
    number = from_wire(Decimal(value))
    if not number.is_finite() or number <= 0:  # checked after the float32 step too
        raise _changed("a close is not positive")
    return number


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


def hint_symbol(hint: Identifier) -> str | None:
    """The Yahoo symbol a hint points to; None when its exchange, type or ticker is not
    usable (e.g. a bond's description, or an exchange outside the table)."""
    if hint.exchange not in HINT_EXCHANGES or hint.security_type not in HINT_TYPES:
        return None
    if not _HINT_TICKER.fullmatch(hint.ticker):
        return None
    return hint.ticker + HINT_EXCHANGES[hint.exchange][0]


def _hint_agrees(meta: dict[str, Any], hint: Identifier) -> bool:
    """Same exchange and same kind of instrument as the hint (D-136)."""
    expected_exchange = HINT_EXCHANGES[hint.exchange or ""][1]
    expected_type = HINT_TYPES[hint.security_type or ""]
    return meta.get("exchangeName") == expected_exchange and (
        meta.get("instrumentType") == expected_type
    )


def _first_text(*values: object) -> str | None:
    return next((v for v in values if isinstance(v, str) and v.strip()), None)


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
    capabilities = CAPABILITIES

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

    def _quotes(self, text: str, max_wait: float | None) -> list[dict[str, Any]]:
        """The usable search results for `text` (a symbol and a known `quoteType`)."""
        text = text.strip()
        if not _QUERY.fullmatch(text):
            return []
        params = {
            "q": text,
            "quotesCount": str(MAX_QUOTES),
            "newsCount": "0",
            "listsCount": "0",
            "enableFuzzyQuery": "false",
        }
        body = self._get("/v1/finance/search", params, max_wait)
        quotes = body.get("quotes") if isinstance(body, dict) else None
        if not isinstance(quotes, list):
            raise _changed("search without a quotes list")
        usable: list[dict[str, Any]] = []
        for item in quotes:
            if not isinstance(item, dict):
                raise _changed("a search quote is not an object")
            if isinstance(item.get("symbol"), str) and item.get("quoteType") in _SEARCHABLE:
                usable.append(item)
        return usable

    def search(self, query: SearchQuery, *, max_wait: float | None = None) -> Sequence[Listing]:
        """Listings for an ISIN or a text, in the rank of D-125: Yahoo's own results and the
        symbols the hints of an ISIN query point to, ranked together before any chart call,
        at most `MAX_CANDIDATES` of them checked (D-136). A candidate Yahoo does not know, or
        whose currency or (for a hint) exchange and type do not fit, is left out."""
        found: dict[str, tuple[dict[str, Any] | None, Identifier | None]] = {}
        for item in self._quotes(query.value, max_wait):
            found.setdefault(item["symbol"], (item, None))
        for hint in query.hints:
            symbol = hint_symbol(hint)
            if symbol is not None:
                found.setdefault(symbol, (None, hint))  # an own result keeps its place

        def rank(entry: tuple[str, tuple[dict[str, Any] | None, Identifier | None]]) -> Rank:
            symbol, (item, hint) = entry
            if hint is not None:
                code, name = HINT_EXCHANGES[hint.exchange or ""][1], None
            else:
                item = item or {}
                code, name = _first_text(item.get("exchange")), _first_text(item.get("exchDisp"))
            return candidate_rank(
                symbol=symbol,
                exchange_code=code,
                exchange_name=name,
                matched_by=MatchSource.HINT if hint else MatchSource.ISIN,
                preferred=query.exchanges,
                main=CAPABILITIES.main_exchanges,
            )

        listings: list[Listing] = []
        for symbol, (item, hint) in sorted(found.items(), key=rank)[:MAX_CANDIDATES]:
            listing = self._candidate(symbol, item, hint, max_wait)
            if listing is not None:
                listings.append(listing)
        return listings

    def _candidate(
        self,
        symbol: str,
        item: dict[str, Any] | None,
        hint: Identifier | None,
        max_wait: float | None,
    ) -> Listing | None:
        try:
            result = self._chart(symbol, {"range": CHECK_RANGE}, max_wait)
        except ProviderError as error:
            if error.code is ErrorCode.NOT_FOUND:
                return None
            raise
        meta = result["meta"]
        if normalize_currency(meta.get("currency")) is None:
            return None  # an unknown currency code is never guessed (D-119)
        if hint is not None and not _hint_agrees(meta, hint):
            return None
        try:
            series = self._parse_closes(result, date.min, date.max)
        except (AttributeError, TypeError, ValueError, OverflowError, OSError, KeyError):
            raise _changed("unexpected chart data") from None
        item = item or {}
        kind = item.get("quoteType") or meta.get("instrumentType")
        return Listing(
            provider=NAME,
            symbol=symbol,
            currency=series.currency,
            unit=series.unit,
            name=_first_text(
                item.get("longname"),
                item.get("shortname"),
                meta.get("longName"),
                meta.get("shortName"),
            ),
            exchange=_first_text(
                item.get("exchDisp"),
                item.get("exchange"),
                meta.get("fullExchangeName"),
                meta.get("exchangeName"),
            ),
            exchange_code=_first_text(meta.get("exchangeName"), item.get("exchange")),
            asset_type=_TYPES.get(kind) if isinstance(kind, str) else None,
            last_close_day=series.closes[-1].day if series.closes else None,
            hint=hint,
        )

    # --- classification ---------------------------------------------------------------

    def classify(self, isin: str) -> SecurityFacts | None:
        """The price class from the first usable result's `quoteType`; None when Yahoo finds
        nothing or the type has no price class (e.g. a mutual fund)."""
        quotes = self._quotes(isin, None)
        if not quotes:
            return None
        price_class = _CLASSES.get(quotes[0]["quoteType"])
        return SecurityFacts(isin, price_class) if price_class else None

    # --- daily closes -----------------------------------------------------------------

    def daily_closes(self, symbol: str, start: date, end: date) -> CloseSeries:
        # One day of margin on each side: bars are stamped at the exchange's opening time,
        # which is on the previous UTC day east of UTC (e.g. Sydney); local dates trim it.
        params = {
            "period1": _epoch(start - timedelta(days=1)),
            "period2": _epoch(end + timedelta(days=2)),
        }
        result = self._chart(symbol, params)
        try:
            return self._parse_closes(result, start, end)
        except ProviderError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError, OSError, KeyError):
            raise _changed("unexpected chart data") from None

    def _parse_closes(self, result: dict[str, Any], start: date, end: date) -> CloseSeries:
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
