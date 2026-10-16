"""Frankfurter v2 adapter against synthetic fixtures (tests/fixtures/marketdata/)."""

from datetime import date
from decimal import Decimal

import pytest
from fakes import FakeTransport, fixture_response, json_response, queue, timeout_error

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError, Rate
from portfolio_tracker.marketdata.frankfurter import DEFAULT_URL, FrankfurterFx
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy

START, END = date(2026, 2, 27), date(2026, 3, 3)


def adapter(*responses, url: str = DEFAULT_URL) -> tuple[FrankfurterFx, FakeTransport]:
    transport = FakeTransport(queue(*responses))
    no_wait = Throttle(ThrottlePolicy(min_interval=0), sleep=lambda _s: None)
    return FrankfurterFx(transport, url, no_wait), transport


def row(day: str, rate: object, quote: str = "USD", base: str = "EUR") -> dict:
    return {"date": day, "base": base, "quote": quote, "rate": rate}


def test_rates_of_one_currency_with_a_weekend_gap() -> None:
    fx, transport = adapter(fixture_response("frankfurter_v2_rates_usd.json"))
    assert fx.rates(["USD"], START, END) == [
        Rate(date(2026, 2, 27), "USD", Decimal("1.1234")),
        Rate(date(2026, 3, 2), "USD", Decimal("1.1250")),
        Rate(date(2026, 3, 3), "USD", Decimal("1.1199")),
    ]
    (call,) = transport.calls
    assert call.url == "https://api.frankfurter.dev/v2/rates"
    assert call.params == {
        "providers": "ecb",
        "base": "EUR",
        "quotes": "USD",
        "from": "2026-02-27",
        "to": "2026-03-03",
    }


def test_rates_keep_every_digit() -> None:
    fx, _ = adapter(json_response([row("2026-03-02", 157.123456)]))
    (rate,) = fx.rates(["USD"], START, END)
    assert str(rate.per_eur) == "157.123456"


def test_one_request_per_currency_and_none_for_eur() -> None:
    fx, transport = adapter(
        json_response([row("2026-03-02", 1.125)]),
        json_response([row("2026-03-02", 0.86, quote="GBP")]),
    )
    rates = fx.rates(["USD", "EUR", "GBP"], START, END)
    assert [(r.currency, r.per_eur) for r in rates] == [
        ("USD", Decimal("1.125")),
        ("GBP", Decimal("0.86")),
    ]
    assert [c.params["quotes"] for c in transport.calls] == ["USD", "GBP"]


def test_a_self_hosted_url() -> None:
    fx, transport = adapter(
        fixture_response("frankfurter_v2_rates_empty.json"), url="http://fx.lan:8080/"
    )
    assert fx.rates(["USD"], START, END) == []
    assert transport.calls[0].url == "http://fx.lan:8080/v2/rates"


def test_an_unknown_currency_has_no_data() -> None:
    fx, _ = adapter(fixture_response("frankfurter_v2_error_422.json", status=422))
    assert fx.rates(["QQQ"], START, END) == []


def test_a_404_is_an_error_not_an_empty_answer() -> None:
    """A wrong PT_FX_URL must show up as failures, not as currencies without data."""
    fx, _ = adapter(json_response({"status": 404, "message": "not found"}, status=404))
    with pytest.raises(ProviderError) as error:
        fx.rates(["USD"], START, END)
    assert error.value.code is ErrorCode.NOT_FOUND


def test_rows_outside_the_range_are_dropped() -> None:
    """A start on a Saturday may be answered from the previous business day."""
    fx, _ = adapter(json_response([row("2026-02-27", 1.1), row("2026-03-02", 1.2)]))
    assert fx.rates(["USD"], date(2026, 2, 28), END) == [
        Rate(date(2026, 3, 2), "USD", Decimal("1.2"))
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"amount": 1.0, "base": "EUR", "rates": {}},  # the v1 shape
        [row("2026-03-02", "1.12")],  # a rate as text
        [row("2026-03-02", -1)],
        [row("2026-03-02", 0)],
        [row("2026-03-02", True)],
        [row("2026-03-02", 1.1, quote="GBP")],
        [row("2026-03-02", 1.1, base="USD")],
        [row("2026-13-02", 1.1)],
        [row("2026-03-02", 1e400)],  # out of bounds
        [{"date": 20260302, "base": "EUR", "quote": "USD", "rate": 1.1}],
        [{"date": "2026-03-02", "quote": "USD", "rate": 1.1}],
        ["2026-03-02"],
    ],
)
def test_a_changed_format_is_detected(body: object) -> None:
    fx, _ = adapter(json_response(body))
    with pytest.raises(ProviderError) as error:
        fx.rates(["USD"], START, END)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


def test_the_v1_fixture_is_a_changed_format() -> None:
    fx, _ = adapter(fixture_response("frankfurter_v1_shape.json"))
    with pytest.raises(ProviderError) as error:
        fx.rates(["USD"], START, END)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


def test_rate_limits_server_errors_and_timeouts_are_errors() -> None:
    fx, _ = adapter(
        json_response({"status": 429, "message": "slow down"}, status=429, Retry_After="60"),
        json_response({"status": 503, "message": "down"}, status=503),
        timeout_error(),
    )
    codes = []
    for _ in range(3):
        with pytest.raises(ProviderError) as error:
            fx.rates(["USD"], START, END)
        codes.append((error.value.code, error.value.retry_after))
    assert codes == [
        (ErrorCode.RATE_LIMITED, 60.0),
        (ErrorCode.HTTP_ERROR, None),
        (ErrorCode.NETWORK, None),
    ]


def test_the_shipped_registry_builds_frankfurter_with_the_configured_url() -> None:
    transport = FakeTransport(queue(fixture_response("frankfurter_v2_rates_empty.json")))
    fx = default_registry().build_fx_provider("frankfurter", transport, "http://self.host")
    assert fx.name == "frankfurter"
    fx.rates(["USD"], START, END)
    assert transport.calls[0].url == "http://self.host/v2/rates"
