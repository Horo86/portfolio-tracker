from datetime import date

from factories import assets_by_id, bond, buy, d, deposit, etf, sell, stock_gbx

from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.valuation import FxRates, PriceHistory, value_account

MON, TUE, WED, SAT, SUN = (date(2026, 6, day) for day in (1, 2, 3, 6, 7))


def test_price_history_carries_forward_and_is_none_before_first_price() -> None:
    history = PriceHistory({TUE: d("10"), SAT: d("11")})
    assert history.on_or_before(MON) is None
    assert history.on_or_before(TUE) == (TUE, d("10"))
    assert history.on_or_before(WED) == (TUE, d("10"))
    assert history.on_or_before(SUN) == (SAT, d("11"))


def test_fx_conversion_through_eur_rounds_once() -> None:
    fx = FxRates({"USD": {MON: d("1.25")}, "GBP": {MON: d("0.85")}})
    assert fx.convert(d("100"), "USD", "EUR", MON) == d("80.00000000")
    assert fx.convert(d("100"), "EUR", "USD", MON) == d("125.00000000")
    # 100 USD -> GBP = 100 x 0.85 / 1.25, no rounding of the cross rate on its own
    assert fx.convert(d("100"), "USD", "GBP", MON) == d("68.00000000")
    assert fx.convert(d("1"), "USD", "GBP", WED) == fx.convert(d("1"), "USD", "GBP", MON)
    assert fx.convert(d("1"), "USD", "EUR", date(2026, 5, 29)) is None  # before the first rate
    assert fx.convert(d("1"), "CHF", "EUR", MON) is None  # unknown currency
    assert fx.convert(d("1.123456789"), "EUR", "EUR", MON) == d("1.123456789")  # untouched


def test_position_without_any_price_is_valued_at_cost() -> None:
    a = etf()
    ledger = replay([buy(a, MON, "10", "100")])
    result = value_account(ledger, assets_by_id(a), {}, FxRates({}), "EUR", WED)
    pv = result.positions[a.id]
    assert (pv.value, pv.unrealized, pv.estimated_at_cost) == (d("100"), 0, True)
    assert not result.partial


def test_before_the_first_price_the_position_is_valued_at_cost() -> None:
    a = etf()
    ledger = replay([buy(a, MON, "10", "100")])
    prices = {a.id: PriceHistory({WED: d("12")})}
    assert (
        value_account(ledger, assets_by_id(a), prices, FxRates({}), "EUR", TUE)
        .positions[a.id]
        .estimated_at_cost
    )
    on_wed = value_account(ledger, assets_by_id(a), prices, FxRates({}), "EUR", WED)
    assert on_wed.positions[a.id].value == d("120")
    assert not on_wed.positions[a.id].estimated_at_cost


def test_missing_fx_makes_the_value_unavailable_and_totals_partial() -> None:
    usd = etf("USD")
    eur = etf()
    ledger = replay([buy(usd, MON, "1", "90"), buy(eur, MON, "1", "50")])
    prices = {usd.id: PriceHistory({MON: d("100")}), eur.id: PriceHistory({MON: d("55")})}
    result = value_account(ledger, assets_by_id(usd, eur), prices, FxRates({}), "EUR", MON)
    assert result.positions[usd.id].value is None
    assert result.positions[usd.id].unrealized is None
    assert result.partial
    assert result.securities_value == d("55")  # available part only, flagged partial
    assert result.unrealized == d("5")


def test_closed_position_has_no_value_and_no_unrealized() -> None:
    a = etf()
    ledger = replay([buy(a, MON, "1", "10"), sell(a, TUE, "1", "12")])
    result = value_account(
        ledger, assets_by_id(a), {a.id: PriceHistory({TUE: d("12")})}, FxRates({}), "EUR", WED
    )
    pv = result.positions[a.id]
    assert (pv.value, pv.unrealized, pv.unrealized_pct) == (0, 0, None)


def test_account_value_includes_cash() -> None:
    a = etf()
    ledger = replay([deposit(MON, "1000"), buy(a, MON, "10", "100")])
    result = value_account(
        ledger, assets_by_id(a), {a.id: PriceHistory({MON: d("11")})}, FxRates({}), "EUR", MON
    )
    assert (result.securities_value, result.cash, result.total_value) == (
        d("110"),
        d("900"),
        d("1010"),
    )


def test_quote_units() -> None:
    b, g = bond(), stock_gbx()
    ledger = replay([buy(b, MON, "1000", "990"), buy(g, MON, "10", "10")])
    prices = {b.id: PriceHistory({MON: d("99.5")}), g.id: PriceHistory({MON: d("120")})}
    fx = FxRates({"GBP": {MON: d("1")}})
    result = value_account(ledger, assets_by_id(b, g), prices, fx, "EUR", MON)
    assert result.positions[b.id].value == d("995")  # 1000 x 99.5 / 100
    assert result.positions[g.id].value == d("12")  # 10 x 120 GBX / 100
