from datetime import date

from factories import assets_by_id, buy, d, deposit, etf

from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.portfolio import AccountInput, aggregate, portfolio_value_on
from portfolio_tracker.engine.valuation import (
    AccountValuation,
    FxRates,
    PriceHistory,
    value_account,
)

DAY = date(2026, 7, 1)


def _usd_account_valuation() -> AccountValuation:
    a = etf("USD")
    ledger = replay([deposit(DAY, "200"), buy(a, DAY, "1", "100")])
    return value_account(
        ledger, assets_by_id(a), {a.id: PriceHistory({DAY: d("120")})}, FxRates({}), "USD", DAY
    )


def test_account_without_fx_is_left_out_and_marks_the_portfolio_partial() -> None:
    usd = _usd_account_valuation()
    eur = value_account(replay([deposit(DAY, "50")]), {}, {}, FxRates({}), "EUR", DAY)
    result = aggregate(
        [AccountInput("USD", usd), AccountInput("EUR", eur)], FxRates({}), "EUR", DAY
    )
    assert result.partial
    assert result.total_value == d("50")
    assert result.unavailable_currencies == ("USD",)


def test_partial_account_valuation_propagates() -> None:
    a = etf("CHF")
    ledger = replay([buy(a, DAY, "1", "10")])
    chf_missing = value_account(
        ledger, assets_by_id(a), {a.id: PriceHistory({DAY: d("11")})}, FxRates({}), "EUR", DAY
    )
    result = aggregate([AccountInput("EUR", chf_missing)], FxRates({}), "EUR", DAY)
    assert result.partial
    assert result.unavailable_currencies == ()


def test_point_value_without_fx_is_partial() -> None:
    usd = _usd_account_valuation()
    value, partial = portfolio_value_on([AccountInput("USD", usd)], FxRates({}), "EUR", DAY)
    assert (value, partial) == (0, True)


def test_partial_account_uses_the_valued_cost_as_unrealized_base() -> None:
    chf, eur = etf("CHF"), etf()
    ledger = replay([buy(chf, DAY, "1", "10"), buy(eur, DAY, "1", "50")])
    prices = {chf.id: PriceHistory({DAY: d("11")}), eur.id: PriceHistory({DAY: d("55")})}
    valuation = value_account(ledger, assets_by_id(chf, eur), prices, FxRates({}), "EUR", DAY)
    assert (valuation.totals.invested_capital, valuation.valued_cost) == (d("60"), d("50"))
    assert valuation.unrealized_pct == d("0.1")  # 5 / 50, not 5 / 60

    result = aggregate([AccountInput("EUR", valuation)], FxRates({}), "EUR", DAY)
    assert result.partial
    assert (result.invested_capital, result.valued_cost) == (d("60"), d("50"))
    assert (result.unrealized, result.unrealized_pct) == (d("5"), d("0.1"))
    assert result.realized_pct_gross is None  # nothing sold: no base (D-57)
