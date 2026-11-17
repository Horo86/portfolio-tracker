"""The live price check (D-178, D-179): a stateless replay of the walk a new instrument gets in
`services/market_data.py`. Parity with `resolve_listings` on the scenarios of the listing
choice, nothing read or stored, the outcomes, the limits and the logs. Fake engines and the
fake provider only; invented ISINs (`ZZ`), symbols and prices."""

import logging
from datetime import date
from decimal import Decimal

import pytest
from engines import (
    BOND_ISIN,
    BONDS,
    EQUITY_UNITS,
    ETF_ISIN,
    NOW,
    FakeClassifier,
    FakePrices,
    built,
    facts,
    holdings,
    listing,
    stored,
)
from fakes import Provider, json_response
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import Engine, text
from test_api_reports import ETF, post

from portfolio_tracker.db import db_transaction
from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.base import (
    Capabilities,
    ErrorCode,
    Identifier,
    PriceClass,
    ProviderError,
    Role,
)
from portfolio_tracker.marketdata.throttle import Stopped
from portfolio_tracker.repositories import instruments
from portfolio_tracker.services import price_check
from portfolio_tracker.services.market_data import link_assets, resolve_listings
from portfolio_tracker.services.price_check import CheckOutcome, CheckUnavailable

ISIN = fake_isin(419)  # the fake provider answers the same for any query
MILAN = Identifier("XMPWM", "MTAA", "ETP", "EXAMPLE WORLD")


class Ticks:
    """A monotonic clock moved by hand (the budget), never by sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def run_check(engines, *, isin=ETF_ISIN, currency="EUR", unit=QuoteUnit.CURRENCY,
              asset_type=AssetType.ETF, exchanges=(), clock=None, **options):  # fmt: skip
    return price_check.check(
        engines,
        exchanges,
        isin=isin,
        currency=currency,
        unit=unit,
        asset_type=asset_type,
        now=NOW,
        clock=clock or Ticks(),
        **options,
    )


# --- parity with the run (D-178) ---------------------------------------------------------------


def _rank_with_a_hint():
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK, MILAN)})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("0XMP.IL", "IOB"), listing("XMPW.DE", "GER"),
                                            listing("XMPWM.MI", "MIL", hint=MILAN)]})  # fmt: skip
    return built([alpha], classify=[figi])


def _two_lines():
    return built([FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL"),
                                                  listing("XMPW.L", "LSE")]})])  # fmt: skip


def _usd_and_london():
    london = Identifier("0XMU", "XLON", "Common Stock")
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK, london)})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("0XMU.L", "LSE", currency="USD", hint=london),
                                            listing("XMPU.DE", "GER"),
                                            listing("XMPU", "NMS", currency="USD")]})  # fmt: skip
    return built([alpha], classify=[figi])


def _percent():
    return listing("XMPB", None, provider="bonds", unit=QuoteUnit.PERCENT_OF_NOMINAL)


def _bond_engine():
    figi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    bonds = FakePrices("bonds", {BOND_ISIN: [_percent()]}, BONDS)
    return built([alpha], [bonds], [figi])


def _unknown_class():
    unknown = FakeClassifier("figi", {ETF_ISIN: None})
    return built([FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})],
                 [FakePrices("bonds", {}, BONDS)], [unknown])  # fmt: skip


def _bond_by_unit():
    bonds = FakePrices("bonds", {BOND_ISIN: [_percent()]}, BONDS)
    return built([FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})], [bonds])


def _both_classes():
    both = FakePrices(
        "both",
        {ETF_ISIN: [listing("XMPW.MI", "MIL", provider="both")]},
        Capabilities(roles=frozenset({Role.ETF_STOCK, Role.BOND}), search_isin=True,
                     units=EQUITY_UNITS | {QuoteUnit.PERCENT_OF_NOMINAL}),
    )  # fmt: skip
    return built([both], [both])


def _confirmed(found):
    def engines():
        figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK, *found[1])})
        return built([FakePrices("alpha", {ETF_ISIN: found[0]})], classify=[figi])

    return engines


XETRA = Identifier("XMPW", "XETR", "ETP")
SCENARIOS = {
    "rank with a hint": (_rank_with_a_hint, ETF_ISIN, "EUR", ()),
    "preferred exchange": (_two_lines, ETF_ISIN, "EUR", ("lse", "MIL")),
    "home currency USD": (_usd_and_london, ETF_ISIN, "USD", ()),
    "home currency EUR": (_usd_and_london, ETF_ISIN, "EUR", ()),
    "bond engine": (_bond_engine, BOND_ISIN, "EUR", ()),
    "unknown class, then the unit": (_unknown_class, ETF_ISIN, "EUR", ()),
    "bond by its unit": (_bond_by_unit, BOND_ISIN, "EUR", ()),
    "one engine, both classes": (_both_classes, ETF_ISIN, "EUR", ()),
    "D-172 another exchange": (_confirmed(([listing("OTHR.DE", "GER"),
                               listing("XMPWM.MI", "MIL", hint=MILAN)], [MILAN])),
                               ETF_ISIN, "EUR", ()),
    "D-172 same exchange": (_confirmed(([listing("OTHR.MI", "MIL"),
                            listing("XMPWM.MI", "MIL", hint=MILAN)], [MILAN])),
                            ETF_ISIN, "EUR", ()),
    "D-172 confirmed by ISIN": (_confirmed(([listing("XMPWM.MI", "MIL"),
                                listing("XMPWM.MI", "MIL", hint=Identifier("xmpwm", "MTAA",
                                "ETP"))], [Identifier("xmpwm", "MTAA", "ETP")])),
                                ETF_ISIN, "EUR", ()),
    "D-172 no confirmed ticker there": (_confirmed(([listing("OTHR.MI", "MIL"),
                                        listing("XMPW.DE", "GER", hint=XETRA)], [XETRA])),
                                        ETF_ISIN, "EUR", ()),
    "hint against ISIN": (_confirmed(([listing("OTHR.MI", "MIL")], [])), ETF_ISIN, "EUR", ()),
}  # fmt: skip


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_check_gives_the_listing_the_run_chooses(db_engine: Engine, name: str) -> None:
    engines_for, isin, currency, exchanges = SCENARIOS[name]
    asset_type = "BOND" if isin == BOND_ISIN else "ETF"
    holdings(db_engine, ("alice", isin, currency, asset_type))
    resolve_listings(db_engine, engines_for(), exchanges, NOW)
    (chosen,) = [row for row in stored(db_engine, isin, currency)[1].values() if row.is_active]

    unit = QuoteUnit.PERCENT_OF_NOMINAL if isin == BOND_ISIN else QuoteUnit.CURRENCY
    result = run_check(engines_for(), isin=isin, currency=currency, unit=unit,
                       asset_type=AssetType(asset_type), exchanges=exchanges)  # fmt: skip
    assert result.outcome is CheckOutcome.FOUND
    active_engine = next(
        p for p, row in stored(db_engine, isin, currency)[1].items() if row.is_active
    )
    assert (result.source, result.exchange_code, result.symbol) == (
        active_engine,
        chosen.exchange_code,
        chosen.symbol,
    )


def test_the_query_is_the_one_of_the_run() -> None:
    engines = _rank_with_a_hint()
    run_check(engines, exchanges=("MIL",))
    (alpha,) = engines.prices(PriceClass.ETF_STOCK)
    (query,) = alpha.queries
    assert (query.isin, query.hints, query.exchanges, query.currency) == (
        ETF_ISIN,
        (MILAN,),
        ("MIL",),
        "EUR",
    )


# --- the outcomes --------------------------------------------------------------------------------


def test_a_found_listing_gives_its_last_close_and_day() -> None:
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL", close=Decimal("24.75"))]})
    result = run_check(built([alpha]))
    assert result == price_check.CheckResult(
        CheckOutcome.FOUND, None, "alpha", "Mil", "MIL", "XMPW.MI", Decimal("24.75"),
        date(2026, 3, 5), "EUR", QuoteUnit.CURRENCY,
    )  # fmt: skip


@pytest.mark.parametrize(
    ("unit", "price"),
    [(QuoteUnit.MINOR_CURRENCY_UNIT, Decimal("1240")), (QuoteUnit.CURRENCY, Decimal("12.40"))],
)
def test_pence_are_rescaled_as_for_stored_prices(unit: QuoteUnit, price: Decimal) -> None:
    pence = listing("XMPW.L", "LSE", currency="GBP", unit=QuoteUnit.MINOR_CURRENCY_UNIT,
                    close=Decimal("1240"))  # fmt: skip
    result = run_check(built([FakePrices("alpha", {ETF_ISIN: [pence]})]), currency="GBP", unit=unit)
    assert (result.outcome, result.price, result.currency, result.quote_unit) == (
        CheckOutcome.FOUND,
        price,
        "GBP",
        unit,
    )
    assert str(result.price) == str(price)  # the decimal point moved, no digit rounded


def test_a_bond_without_a_bond_engine_is_no_engine_after_one_classifier_call() -> None:
    figi = FakeClassifier("figi", {BOND_ISIN: facts(BOND_ISIN, PriceClass.BOND)})
    later = FakeClassifier("later", {})
    alpha = FakePrices("alpha", {BOND_ISIN: [listing("XMPB.MI", "MIL")]})
    result = run_check(built([alpha], [], [figi, later]), isin=BOND_ISIN,
                       unit=QuoteUnit.PERCENT_OF_NOMINAL, asset_type=AssetType.BOND)  # fmt: skip
    assert (result.outcome, result.reason) == (CheckOutcome.NO_SOURCE, "NO_ENGINE")
    assert figi.asked == [BOND_ISIN] and later.asked == [] and alpha.queries == []


def test_without_any_price_engine_no_classifier_is_asked() -> None:
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    result = run_check(built(classify=[figi]))
    assert (result.outcome, result.reason) == (CheckOutcome.NO_SOURCE, "NO_ENGINE")
    assert figi.asked == []


def test_nothing_found_is_not_found() -> None:
    result = run_check(built([FakePrices("alpha", {})]))
    assert (result.outcome, result.reason) == (CheckOutcome.NO_SOURCE, "NOT_FOUND")


def test_candidates_only_in_another_currency_are_a_currency_mismatch() -> None:
    usd = FakePrices("alpha", {ETF_ISIN: [listing("XMPU", "NMS", currency="USD")]})
    result = run_check(built([usd]))
    assert (result.outcome, result.reason) == (CheckOutcome.MISMATCH, "CURRENCY")
    assert result.symbol is None and result.price is None


def test_an_asset_held_as_a_bond_against_a_listing_in_currency_is_a_unit_mismatch() -> None:
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL", close=Decimal("24.75"))]})
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    result = run_check(built([alpha], classify=[figi]), unit=QuoteUnit.PERCENT_OF_NOMINAL,
                       asset_type=AssetType.BOND)  # fmt: skip
    assert (result.outcome, result.reason) == (CheckOutcome.MISMATCH, "UNIT")
    assert result.price is None


def test_a_unit_the_engine_does_not_fit_is_a_unit_mismatch() -> None:
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    percent = FakePrices(
        "alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL", unit=QuoteUnit.PERCENT_OF_NOMINAL)]}
    )
    result = run_check(built([percent], classify=[figi]))
    assert (result.outcome, result.reason) == (CheckOutcome.MISMATCH, "UNIT")


# --- unavailable -------------------------------------------------------------------------------


def test_a_failing_classifier_carrying_the_isin_makes_it_unavailable() -> None:
    figi = FakeClassifier("figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "down")})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    with pytest.raises(CheckUnavailable):
        run_check(built([alpha], classify=[figi]))
    assert alpha.queries == []  # the run would postpone: hints lost


def test_no_class_while_a_classifier_failed_makes_it_unavailable() -> None:
    figi = FakeClassifier(
        "figi", {ETF_ISIN: ProviderError(ErrorCode.NETWORK, "down")}, echoes_isin=False
    )
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    with pytest.raises(CheckUnavailable):
        run_check(built([alpha], classify=[figi]))
    assert alpha.queries == []


def test_the_first_engine_error_makes_it_unavailable() -> None:
    failing = FakePrices("alpha", {ETF_ISIN: ProviderError(ErrorCode.HTTP_ERROR, "500")})
    other = FakePrices("beta", {ETF_ISIN: [listing("XMPW.MI", "MIL", provider="beta")]})
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    with pytest.raises(CheckUnavailable) as raised:
        run_check(built([failing, other], classify=[figi]))
    assert raised.value.code == "MARKET_DATA_UNAVAILABLE" and other.queries == []


def test_an_adapter_bug_makes_it_unavailable() -> None:
    class Broken(FakePrices):
        def search(self, query, *, max_wait=None):
            raise KeyError(ETF_ISIN)

    with pytest.raises(CheckUnavailable):
        run_check(built([Broken("alpha", {})]))


def test_a_shutdown_makes_it_unavailable() -> None:
    def stop() -> None:
        raise Stopped

    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]}, during=stop)
    with pytest.raises(CheckUnavailable):
        run_check(built([alpha]))


# --- max_wait and the budget (D-179) -----------------------------------------------------------


def test_max_wait_reaches_the_classifier_and_the_engine() -> None:
    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)})
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    run_check(built([alpha], classify=[figi]), max_wait=7.0)
    assert figi.waits == [7.0] and alpha.waits == [7.0]


def test_max_wait_never_goes_past_the_budget() -> None:
    clock = Ticks()

    def slow() -> None:
        clock.now += 15.0

    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)}, during=slow)
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    run_check(built([alpha], classify=[figi]), clock=clock)
    assert figi.waits == [10.0] and alpha.waits == [5.0]  # 20 s in all


def test_past_the_20_second_budget_no_further_call_is_made() -> None:
    clock = Ticks()

    def slow() -> None:
        clock.now += 20.0

    figi = FakeClassifier("figi", {ETF_ISIN: facts(ETF_ISIN, PriceClass.ETF_STOCK)}, during=slow)
    alpha = FakePrices("alpha", {ETF_ISIN: [listing("XMPW.MI", "MIL")]})
    with pytest.raises(CheckUnavailable) as raised:
        run_check(built([alpha], classify=[figi]), clock=clock)
    assert raised.value.code == "MARKET_DATA_UNAVAILABLE" and alpha.queries == []


def test_without_max_wait_the_run_calls_adapters_as_before() -> None:
    """The run passes no `max_wait`: an adapter without the keyword still works there."""

    class Plain:
        name = "plain"
        capabilities = Capabilities(roles=frozenset({Role.CLASSIFY}), echoes_isin=True)

        def classify(self, isin):
            return facts(isin, PriceClass.ETF_STOCK)

    from collections import Counter

    from portfolio_tracker.services.listing_choice import classify

    said = classify(built(classify=[Plain()]), ETF_ISIN, Counter())
    assert said.price_class is PriceClass.ETF_STOCK


# --- the route ---------------------------------------------------------------------------------

BODY = {"isin": ISIN, "quote_currency": "EUR", "quote_unit": "CURRENCY", "asset_type": "ETF"}


def check(client: TestClient, body=None, headers=CSRF, expected: int = 200) -> dict:
    response = client.post("/api/v1/assets/price-check", headers=headers, json=body or BODY)
    assert response.status_code == expected, response.text
    return response.json()


def test_the_route_gives_the_expected_source_exchange_and_price(on: TestClient) -> None:
    assert check(on) == {
        "outcome": "FOUND",
        "reason": None,
        "source": "yahoo",
        "exchange": "Milan",
        "exchange_code": "MIL",
        "symbol": "XMPW.MI",
        "price": "98.8",
        "price_date": "2026-03-02",
        "currency": "EUR",
        "quote_unit": "CURRENCY",
    }


def test_the_route_rescales_pence(on: TestClient) -> None:
    body = BODY | {"quote_currency": "GBP", "quote_unit": "MINOR_CURRENCY_UNIT"}
    found = check(on, body)
    assert (found["symbol"], found["price"], found["quote_unit"]) == (
        "XMPW.L",
        "1240",
        "MINOR_CURRENCY_UNIT",
    )
    found = check(on, body | {"quote_unit": "CURRENCY"})
    assert (found["price"], found["quote_unit"]) == ("12.40", "CURRENCY")


def _counts(client: TestClient) -> dict[str, int]:
    with db_transaction(client.app.state.engine, write=False) as session:
        tables = session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        ).scalars()
        return {
            t: session.execute(text(f'SELECT count(*) FROM "{t}"')).scalar_one() for t in tables
        }


def test_a_check_stores_nothing(on: TestClient) -> None:
    before = _counts(on)
    assert "instruments" in before and "audit_log" in before
    check(on)
    assert _counts(on) == before


def test_the_answer_never_depends_on_what_another_user_holds(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bob holds the ISIN, linked with another active listing (Xetra): Alice's check still
    gives what a new instrument would get (D-116, D-178)."""
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    alice = investor(on, "alice")
    before = check(on, headers=alice)

    bob = investor(on, "bob")
    post(on, "/api/v1/assets", ETF | {"isin": ISIN}, bob)
    engine = on.app.state.engine
    link_assets(engine)
    with db_transaction(engine, write=True) as session:
        instrument = instruments.get_instrument(session, ISIN, "EUR")
        instruments.set_price_class(session, instrument, PriceClass.ETF_STOCK, "openfigi")
        instruments.save_check(session, instrument.id, "yahoo", status="OK", symbol="XMPW.DE",
                               mismatch=None, checked_at=NOW)  # fmt: skip
        stored_listing = instruments.save_listing(
            session, instrument.id, "yahoo", "XMPW.DE", exchange="XETRA", exchange_code="GER",
            currency="EUR", unit="CURRENCY", status="OK", mismatch=None,
        )  # fmt: skip
        instruments.activate(session, stored_listing, NOW)

    assert check(on, headers=alice) == before
    assert before["symbol"] == "XMPW.MI"


class FailingProvider(Provider):
    """The fake provider, with the calls to hosts containing `fail` answering 500."""

    def __init__(self, fail: str) -> None:
        super().__init__()
        self.fail = fail

    def __call__(self, request):
        if self.fail in request.url:
            self.calls.append(request)
            return json_response({}, status=500)
        return super().__call__(request)


@pytest.mark.parametrize("fail", ["openfigi", "/v8/finance/chart/"])
def test_a_failing_classifier_or_engine_gives_503_unavailable(market_app_with, fail: str) -> None:
    with market_app_with(FailingProvider(fail)) as client:
        failed = check(client, expected=503)
    assert failed["code"] == "MARKET_DATA_UNAVAILABLE"


def test_six_checks_a_minute_per_user(on: TestClient, clock) -> None:
    for _ in range(6):
        check(on)
    response = on.post("/api/v1/assets/price-check", headers=CSRF, json=BODY)
    assert response.status_code == 429 and response.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert response.headers["Retry-After"] == "60"
    check(on, headers=investor(on, "bob"))  # another user has their own limit
    clock.advance(seconds=60)
    check(on)


def test_a_third_check_at_once_gives_503_busy(on: TestClient) -> None:
    slots = on.app.state.price_check_slots
    assert slots.acquire(blocking=False) and slots.acquire(blocking=False)  # two in flight
    try:
        response = on.post("/api/v1/assets/price-check", headers=CSRF, json=BODY)
        assert response.status_code == 503 and response.json()["code"] == "MARKET_DATA_BUSY"
        assert response.headers["Retry-After"] == "5"
    finally:
        slots.release()
        slots.release()
    check(on)  # the slots are free again, and a check releases its own
    check(on)
    check(on)


def test_with_market_data_off_the_check_answers_so(client: TestClient) -> None:
    create_admin(client)
    response = client.post("/api/v1/assets/price-check", headers=CSRF, json=BODY)
    assert response.status_code == 503 and response.json()["code"] == "MARKET_DATA_OFF"


def test_no_database_transaction_is_open_while_the_provider_answers(
    on: TestClient, answers: Provider
) -> None:
    pool = on.app.state.engine.pool
    seen: list[int] = []
    answers.during = lambda: seen.append(pool.checkedout())
    check(on)
    assert seen and set(seen) == {0}


@pytest.fixture
def switchable(market_app_with):
    """The app on a provider whose charts can be made to fail; started before `caplog`, so
    the app's own logging set-up leaves the capture in place."""
    answers = FailingProvider("nowhere")
    with market_app_with(answers) as client:
        yield client, answers


def test_no_isin_symbol_or_price_reaches_the_logs(
    switchable, caplog: pytest.LogCaptureFixture
) -> None:
    client, answers = switchable
    with caplog.at_level(logging.DEBUG):
        found = check(client)
        answers.fail = "/v8/finance/chart/"
        failed = client.post("/api/v1/assets/price-check", headers=CSRF, json=BODY)
    assert found["symbol"] == "XMPW.MI" and failed.status_code == 503
    assert ISIN not in failed.text and "XMPW" not in failed.text
    logged = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert "listing search failed" in logged and "price check unavailable" in logged
    for secret in (ISIN, "XMPW", "98.8"):
        assert secret not in logged
