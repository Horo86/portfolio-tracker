"""Yahoo adapter (unofficial) against synthetic fixtures; throttle timing on a fake clock."""

from datetime import date
from decimal import Decimal

import pytest
from fakes import FakeRequest, FakeTransport, fixture_response, json_response, queue
from fakes import timeout_error as timeout

from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import Close, CloseSeries, ErrorCode, ProviderError
from portfolio_tracker.marketdata.http import HttpResponse
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy
from portfolio_tracker.marketdata.units import Mismatch, compatible
from portfolio_tracker.marketdata.yahoo import MAX_CANDIDATES, YahooPrices, from_wire

ISIN = "ZZ0000000418"  # invented ("ZZ" is no country)


class FakeTime:
    def __init__(self) -> None:
        self.now, self.sleeps = 0.0, []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def adapter(*responses: HttpResponse | Exception, interval: float = 0.0):
    time = FakeTime()
    transport = FakeTransport(queue(*responses))
    throttle = Throttle(ThrottlePolicy(min_interval=interval), time.clock, time.sleep)
    return YahooPrices(transport, throttle), transport, time


def chart(name: str, status: int = 200) -> HttpResponse:
    return fixture_response(f"yahoo_chart_{name}.json", status=status)


def path(call: FakeRequest) -> str:
    return call.url.removeprefix("https://query2.finance.yahoo.com")


# --- search ---------------------------------------------------------------------------------


def test_search_by_isin_reads_each_listing_currency() -> None:
    yahoo, transport, _ = adapter(
        fixture_response("yahoo_search_isin.json"),
        chart("eur_etf"),
        chart("gbp_pence"),
        chart("eur_milan"),
    )
    listings = yahoo.search(f" {ISIN} ")

    assert [(x.symbol, x.currency, x.unit, x.exchange) for x in listings] == [
        ("XMPW.DE", "EUR", QuoteUnit.CURRENCY, "XETRA"),
        ("XMPW.L", "GBP", QuoteUnit.MINOR_CURRENCY_UNIT, "London"),
        ("XMPW.MI", "EUR", QuoteUnit.CURRENCY, "Milan"),
    ]  # the index quote is left out
    assert {x.asset_type for x in listings} == {AssetType.ETF}
    assert {x.name for x in listings} == {"Example World Equity UCITS ETF Acc"}
    assert {x.provider for x in listings} == {"yahoo"}
    search, *charts = transport.calls
    assert path(search) == "/v1/finance/search"
    assert search.params["q"] == ISIN and search.params["enableFuzzyQuery"] == "false"
    assert [path(c) for c in charts] == [
        "/v8/finance/chart/XMPW.DE",
        "/v8/finance/chart/XMPW.L",
        "/v8/finance/chart/XMPW.MI",
    ]
    assert charts[0].params == {"interval": "1d", "range": "5d"}


def test_search_without_results_makes_one_request() -> None:
    yahoo, transport, _ = adapter(fixture_response("yahoo_search_empty.json"))
    assert yahoo.search("NOTHING") == []
    assert len(transport.calls) == 1


@pytest.mark.parametrize("query", ["", "   ", "<script>", "a" * 41, "x/../y", "é"])
def test_unusable_queries_send_nothing(query: str) -> None:
    yahoo, transport, _ = adapter()
    assert yahoo.search(query) == []
    assert transport.calls == []


def test_candidates_without_chart_or_with_an_unknown_currency_are_skipped() -> None:
    unknown = json_response({"chart": {"result": [{"meta": {"currency": "ZAc"}}], "error": None}})
    yahoo, _, _ = adapter(
        fixture_response("yahoo_search_isin.json"),
        chart("not_found", status=404),
        unknown,
        chart("eur_milan"),
    )
    assert [x.symbol for x in yahoo.search(ISIN)] == ["XMPW.MI"]


def test_search_reads_at_most_a_few_candidates() -> None:
    quotes = [
        {"symbol": f"XMP{i}.DE", "quoteType": "EQUITY", "exchDisp": "XETRA"} for i in range(10)
    ]
    yahoo, transport, _ = adapter(
        json_response({"quotes": quotes}), *[chart("eur_etf")] * MAX_CANDIDATES
    )
    listings = yahoo.search("XMP")
    assert len(listings) == MAX_CANDIDATES == len(transport.calls) - 1
    assert {x.asset_type for x in listings} == {AssetType.STOCK}


def test_a_changed_search_format_is_detected() -> None:
    yahoo, _, _ = adapter(json_response({"items": []}))
    with pytest.raises(ProviderError) as error:
        yahoo.search(ISIN)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


# --- daily closes ---------------------------------------------------------------------------


def test_daily_closes_are_raw_closes_dated_on_the_exchange() -> None:
    yahoo, transport, _ = adapter(chart("eur_etf"))
    series = yahoo.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 5))

    assert series == CloseSeries(
        "EUR",
        QuoteUnit.CURRENCY,
        (
            Close(date(2026, 3, 2), Decimal("98.74")),  # float32 serialisation undone
            Close(date(2026, 3, 4), Decimal("99.1")),  # the null close of 3 March is absent
            Close(date(2026, 3, 5), Decimal("101.5")),
        ),
    )
    (call,) = transport.calls
    assert path(call) == "/v8/finance/chart/XMPW.DE"
    assert call.params == {"interval": "1d", "period1": "1772409600", "period2": "1772755200"}


def test_closes_outside_the_range_are_dropped() -> None:
    yahoo, _, _ = adapter(chart("eur_etf"))
    series = yahoo.daily_closes("XMPW.DE", date(2026, 3, 4), date(2026, 3, 4))
    assert series.closes == (Close(date(2026, 3, 4), Decimal("99.1")),)


def test_pence_listings_report_their_unit() -> None:
    yahoo, _, _ = adapter(chart("gbp_pence"))
    series = yahoo.daily_closes("XMPW.L", date(2026, 3, 1), date(2026, 3, 3))
    assert (series.currency, series.unit) == ("GBP", QuoteUnit.MINOR_CURRENCY_UNIT)
    assert [c.close for c in series.closes] == [Decimal("1234.5"), Decimal("1240")]


def test_a_market_opening_before_midnight_utc_keeps_its_local_date() -> None:
    yahoo, _, _ = adapter(chart("sydney"))
    series = yahoo.daily_closes("XMPA.AX", date(2026, 3, 1), date(2026, 3, 3))
    assert [c.day for c in series.closes] == [date(2026, 3, 2), date(2026, 3, 3)]


def test_a_range_without_trading_has_no_closes() -> None:
    yahoo, _, _ = adapter(chart("no_data"))
    assert yahoo.daily_closes("XMPW.DE", date(2026, 3, 7), date(2026, 3, 8)).closes == ()


def test_a_bond_listing_never_claims_percent_of_nominal() -> None:
    yahoo, _, _ = adapter(chart("bond"))
    series = yahoo.daily_closes("XMPB.MI", date(2026, 3, 2), date(2026, 3, 2))
    assert series.unit is QuoteUnit.CURRENCY
    rule = compatible(
        "EUR", QuoteUnit.PERCENT_OF_NOMINAL, AssetType.BOND, series.currency, series.unit
    )
    assert isinstance(rule, Mismatch)  # the bond keeps its manual prices (D-118)


@pytest.mark.parametrize(
    "response",
    [
        fixture_response("yahoo_chart_not_found.json", status=404),
        fixture_response("yahoo_chart_not_found.json", status=200),
    ],
)
def test_an_unknown_symbol_is_not_found(response: HttpResponse) -> None:
    yahoo, _, _ = adapter(response)
    with pytest.raises(ProviderError) as error:
        yahoo.daily_closes("GONE.DE", date(2026, 3, 2), date(2026, 3, 5))
    assert error.value.code is ErrorCode.NOT_FOUND


def test_a_symbol_that_is_not_a_symbol_sends_nothing() -> None:
    yahoo, transport, _ = adapter()
    with pytest.raises(ProviderError) as error:
        yahoo.daily_closes("../v1/finance/search", date(2026, 3, 2), date(2026, 3, 5))
    assert error.value.code is ErrorCode.NOT_FOUND and transport.calls == []


def _chart_body(closes: list, timestamps: list | None = None, currency: object = "EUR") -> dict:
    stamps = [1772438400, 1772524800][: len(closes)] if timestamps is None else timestamps
    meta = {"currency": currency, "exchangeTimezoneName": "Europe/Berlin"}
    result = {"meta": meta, "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}
    return {"chart": {"result": [result], "error": None}}


@pytest.mark.parametrize(
    "body",
    [
        _chart_body([98.74], currency=None),
        _chart_body([98.74], currency="XYZ"),
        _chart_body([98.74, 99.0], timestamps=[1772438400]),
        _chart_body([-1]),
        _chart_body([0]),
        _chart_body(["98.74"]),
        _chart_body([98.74], timestamps=["1772438400"]),
        {"chart": {"result": [], "error": None}},
        {"chart": {"result": None, "error": {"code": "Bad Request"}}},
        {"finance": {}},
    ],
)
def test_a_changed_chart_format_is_detected(body: dict) -> None:
    yahoo, _, _ = adapter(json_response(body))
    with pytest.raises(ProviderError) as error:
        yahoo.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 5))
    assert error.value.code is ErrorCode.FORMAT_CHANGED


def test_the_changed_fixture_is_detected() -> None:
    yahoo, _, _ = adapter(chart("changed"))
    with pytest.raises(ProviderError) as error:
        yahoo.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 5))
    assert error.value.code is ErrorCode.FORMAT_CHANGED


# --- errors and throttle --------------------------------------------------------------------


def test_429_5xx_and_timeouts_are_errors_and_pause_the_next_call() -> None:
    yahoo, _, time = adapter(
        json_response({}, status=429, Retry_After="20"),
        json_response({}, status=502),
        timeout(),
        chart("eur_etf"),
        interval=1.0,
    )
    codes = []
    for _ in range(3):
        with pytest.raises(ProviderError) as error:
            yahoo.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 5))
        codes.append(error.value.code)
    assert codes == [ErrorCode.RATE_LIMITED, ErrorCode.HTTP_ERROR, ErrorCode.NETWORK]
    assert yahoo.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 5)).closes
    assert time.sleeps == [20.0, 4.0, 8.0]  # Retry-After, then the doubling back-off


def test_calls_are_spaced_by_the_throttle() -> None:
    yahoo, _, time = adapter(
        fixture_response("yahoo_search_isin.json"),
        chart("eur_etf"),
        chart("gbp_pence"),
        chart("eur_milan"),
        interval=1.5,
    )
    yahoo.search(ISIN)
    assert time.sleeps == [1.5, 1.5, 1.5]


def test_a_search_that_cannot_wait_is_refused() -> None:
    yahoo, transport, _ = adapter(json_response({}, status=429, Retry_After="30"), interval=1.0)
    with pytest.raises(ProviderError):
        yahoo.search(ISIN)
    with pytest.raises(ProviderError) as error:
        yahoo.search(ISIN, max_wait=2)
    assert error.value.retry_after == 30 and len(transport.calls) == 1


def test_the_shipped_registry_has_yahoo() -> None:
    transport = FakeTransport(queue(chart("eur_etf")))
    built = default_registry().build_price_providers(("yahoo",), transport)
    (provider,) = built.providers
    assert provider.name == "yahoo"
    assert provider.daily_closes("XMPW.DE", date(2026, 3, 2), date(2026, 3, 2)).closes


# --- float32 serialisation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wire", "expected"),
    [
        ("98.73999786376953", "98.74"),
        ("98.80000305175781", "98.8"),
        ("0.10000000149011612", "0.1"),
        ("1234.5", "1234.5"),  # exact in float32 already
        ("100", "100"),
        ("98.74", "98.74"),  # a double, not a float32: kept as received
        ("271.86", "271.86"),
        ("1E+40", "1E+40"),  # beyond float32: kept
    ],
)
def test_float32_serialisation_is_undone_only_when_exact(wire: str, expected: str) -> None:
    assert str(from_wire(Decimal(wire))) == expected
