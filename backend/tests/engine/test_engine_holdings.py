"""Holdings over all accounts in the reporting currency (D-143). Invented figures."""

from datetime import date
from uuid import uuid4

from factories import assets_by_id, bond, buy, d, deposit, dividend, etf, sell

from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.portfolio import AccountPositions, aggregate_positions
from portfolio_tracker.engine.valuation import FxRates, PositionValue, PriceHistory, value_account

DAY = date(2026, 7, 1)
USD_RATE = FxRates({"USD": {date(2026, 6, 30): d("1.25")}})  # 1.25 USD per EUR


def _account(currency, transactions, assets, prices, fx=USD_RATE) -> AccountPositions:
    ledger = replay(transactions)
    histories = {a.id: PriceHistory({DAY: d(p)}) for a, p in zip(assets, prices, strict=True)}
    valuation = value_account(ledger, assets_by_id(*assets), histories, fx, currency, DAY)
    return AccountPositions(uuid4(), currency, ledger.positions, valuation.positions)


def test_two_euro_accounts_are_summed_and_the_average_cost_divided_once() -> None:
    a = etf()
    first = _account("EUR", [buy(a, DAY, "10", "1000", fees="5")], [a], ["110"])
    second = _account(
        "EUR",
        [
            buy(a, date(2026, 1, 5), "5", "520"),
            sell(a, date(2026, 3, 2), "2", "230", taxes="3"),  # cost removed 520 x 2 / 5 = 208
            dividend(a, date(2026, 4, 1), "10", taxes="2.6"),
        ],
        [a],
        ["110"],
    )
    result = aggregate_positions([first, second], USD_RATE, "EUR", DAY)
    assert not result.partial
    assert result.unavailable_currencies == ()
    row = result.positions[a.id]
    assert row.account_ids == (first.account_id, second.account_id)
    assert row.quantity == d("13")
    assert row.cost_basis == d("1317")  # 1005 + 312
    assert row.pmc == d("101.30769231")  # 1317 / 13, 8 decimals half-even
    assert row.value == d("1430")  # 13 x 110
    assert (row.unrealized, row.unrealized_pct) == (d("113"), d("0.08580106"))
    assert (row.realized_gross, row.realized_taxes, row.realized_net) == (d(22), d(3), d(19))
    assert (row.realized_pct_gross, row.realized_pct_net) == (d("0.10576923"), d("0.09134615"))
    assert (row.income_gross, row.income_taxes, row.income_net) == (d(10), d("2.6"), d("7.4"))
    assert row.taxes == d("5.6")
    assert (row.gain_gross, row.gain_net) == (d("145"), d("139.4"))  # 113 + 22 + 10, - 5.6
    assert not row.partial and not row.estimated_at_cost


def test_a_usd_asset_in_a_euro_and_a_dollar_account_is_converted_at_the_latest_rate() -> None:
    u = etf("USD")
    in_euro = _account("EUR", [buy(u, DAY, "10", "800")], [u], ["100"])  # value 1000 USD = 800 EUR
    in_dollars = _account("USD", [buy(u, DAY, "5", "450")], [u], ["100"])  # cost 360, value 400
    result = aggregate_positions([in_euro, in_dollars], USD_RATE, "EUR", DAY)
    row = result.positions[u.id]
    assert row.quantity == d("15")
    assert (row.cost_basis, row.value) == (d("1160"), d("1200"))
    assert row.pmc == d("77.33333333")  # in EUR, not in the quote currency (§9)
    assert (row.unrealized, row.unrealized_pct) == (d("40"), d("0.03448276"))
    assert not result.partial


def test_an_account_without_a_rate_is_left_out_and_the_result_is_partial() -> None:
    u, a = etf("USD"), etf()
    in_euro = _account("EUR", [buy(a, DAY, "1", "50")], [a], ["55"], FxRates({}))
    in_dollars = _account(
        "USD", [buy(u, DAY, "5", "450"), buy(a, DAY, "1", "60")], [u, a], ["100", "55"], FxRates({})
    )
    result = aggregate_positions([in_euro, in_dollars], FxRates({}), "EUR", DAY)
    assert result.partial
    assert result.unavailable_currencies == ("USD",)
    assert set(result.positions) == {a.id}  # the dollar account's part is not in any row
    row = result.positions[a.id]
    assert row.account_ids == (in_euro.account_id,)
    assert (row.quantity, row.cost_basis, row.value) == (d(1), d(50), d(55))


def test_a_part_that_cannot_be_valued_leaves_the_valued_parts_and_marks_the_row() -> None:
    a = etf("CHF")
    first, second = uuid4(), uuid4()
    ledger_one = replay([buy(a, DAY, "2", "20")]).positions
    ledger_two = replay([buy(a, DAY, "3", "36")]).positions
    valued = {a.id: PositionValue(a.id, d(20), d(22), False, DAY)}
    unvalued = {a.id: PositionValue(a.id, d(36), None, False, DAY)}  # no CHF rate
    result = aggregate_positions(
        [
            AccountPositions(first, "EUR", ledger_one, valued),
            AccountPositions(second, "EUR", ledger_two, unvalued),
        ],
        FxRates({}),
        "EUR",
        DAY,
    )
    assert result.partial
    assert result.unavailable_currencies == ()
    row = result.positions[a.id]
    assert row.partial
    assert (row.quantity, row.cost_basis) == (d(5), d(56))
    assert (row.value, row.valued_cost) == (d(22), d(20))
    assert (row.unrealized, row.unrealized_pct) == (d(2), d("0.1"))  # on the valued cost (D-64)
    assert row.gain_gross == d(2)


def test_nothing_valued_gives_no_value_and_no_gain() -> None:
    a = etf("CHF")
    ledger = replay([buy(a, DAY, "2", "20")]).positions
    unvalued = {a.id: PositionValue(a.id, d(20), None, False, DAY)}
    result = aggregate_positions(
        [AccountPositions(uuid4(), "EUR", ledger, unvalued)], FxRates({}), "EUR", DAY
    )
    row = result.positions[a.id]
    assert (row.value, row.unrealized, row.unrealized_pct) == (None, None, None)
    assert (row.gain_gross, row.gain_net) == (None, None)
    assert row.partial


def test_a_bond_average_cost_stays_per_unit_of_nominal() -> None:
    b = bond()
    first = _account("EUR", [buy(b, DAY, "1000", "976")], [b], ["98"])
    second = _account("EUR", [buy(b, DAY, "1000", "990")], [b], ["98"])
    row = aggregate_positions([first, second], USD_RATE, "EUR", DAY).positions[b.id]
    assert row.pmc == d("0.983")  # the API shows 98.3 % of nominal (D-66)
    assert row.value == d("1960")


def test_a_closed_position_and_one_without_a_price() -> None:
    a, b = etf(), etf()
    ledger = replay([buy(a, DAY, "2", "20"), sell(a, DAY, "2", "26"), buy(b, DAY, "1", "40")])
    valuation = value_account(
        ledger, assets_by_id(a, b), {}, USD_RATE, "EUR", DAY
    )  # no price at all: b is estimated at cost (D-46)
    result = aggregate_positions(
        [AccountPositions(uuid4(), "EUR", ledger.positions, valuation.positions)],
        USD_RATE,
        "EUR",
        DAY,
    )
    closed, unpriced = result.positions[a.id], result.positions[b.id]
    assert (closed.quantity, closed.cost_basis, closed.pmc) == (d(0), d(0), None)
    assert (closed.value, closed.realized_gross) == (d(0), d(6))
    assert unpriced.estimated_at_cost
    assert (unpriced.value, unpriced.unrealized) == (d(40), d(0))
    assert not result.partial


def test_an_account_without_positions_does_not_count() -> None:
    """D-153: cash alone in a currency without a rate leaves no holding out."""
    a = etf()
    in_euro = _account("EUR", [buy(a, DAY, "1", "50")], [a], ["55"], FxRates({}))
    only_cash = _account("GBP", [deposit(DAY, "10")], [], [], FxRates({}))
    result = aggregate_positions([in_euro, only_cash], FxRates({}), "EUR", DAY)
    assert not result.partial
    assert result.unavailable_currencies == ()
    assert set(result.positions) == {a.id}
