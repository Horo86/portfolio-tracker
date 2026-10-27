"""Allocation and income history of a view (domain-rules §13, D-153). Invented figures."""

import dataclasses
from datetime import date
from uuid import uuid4

from factories import assets_by_id, bond, buy, coupon, d, deposit, dividend, etf, sell, withdrawal

from portfolio_tracker.engine.breakdown import CASH, breakdown, breakdown_input
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.model import Asset, AssetType, QuoteUnit
from portfolio_tracker.engine.valuation import FxRates, PriceHistory, value_account

DAY = date(2026, 10, 20)
USD_RATE = FxRates({"USD": {date(2026, 10, 19): d("1.25")}})  # 1.25 USD per EUR


def stock(quote_currency: str = "EUR") -> Asset:
    return Asset(uuid4(), AssetType.STOCK, quote_currency, QuoteUnit.CURRENCY)


def account(currency, transactions, assets=(), prices=None, fx=USD_RATE):
    """One account's input, its securities priced on DAY as given ({asset: close})."""
    ledger = replay(transactions)
    histories = {a.id: PriceHistory({DAY: d(p)}) for a, p in (prices or {}).items()}
    valuation = value_account(ledger, assets_by_id(*assets), histories, fx, currency, DAY)
    return breakdown_input(currency, valuation, assets_by_id(*assets), transactions)


def shares(items):
    return [(item.key, item.value, item.share) for item in items]


def test_one_account_counts_values_cost_and_cash_largest_first() -> None:
    x, y, gone = etf(), stock(), etf()
    eur = account(
        "EUR",
        [
            deposit(DAY, "1000"),
            buy(x, DAY, "5", "500"),
            buy(y, DAY, "3", "300"),  # no price: valued at cost (D-46)
            buy(gone, date(2026, 1, 5), "1", "50"),
            sell(gone, date(2026, 2, 5), "1", "55"),  # closed: no value, not listed
        ],
        [x, y, gone],
        {x: "120"},
    )
    result = breakdown([eur], USD_RATE, "EUR", DAY)
    assert [s.key for s in result.by_type] == ["ETF", "STOCK", CASH]
    assert [s.value for s in result.by_type] == [d("600"), d("300"), d("205")]  # cash 1000-850+55
    assert [s.share for s in result.by_type] == [d("0.54298643"), d("0.27149321"), d("0.18552036")]
    assert shares(result.by_currency) == [("EUR", d("1105"), d("1"))]
    assert shares(result.by_asset) == [
        (x.id, d("600"), d("0.54298643")),
        (y.id, d("300"), d("0.27149321")),
    ]
    assert result.negative_cash == 0
    assert not result.partial and result.unavailable_currencies == ()


def test_negative_cash_is_left_out_of_both_allocations_and_reported() -> None:
    u = etf("USD")
    usd = account("USD", [buy(u, DAY, "2", "250")], [u], {u: "150"})  # cash -250 USD
    result = breakdown([usd], USD_RATE, "EUR", DAY)
    assert shares(result.by_type) == [("ETF", d("240"), d("1"))]  # 300 USD / 1.25
    assert shares(result.by_currency) == [("USD", d("240"), d("1"))]
    assert result.negative_cash == d("-200")


def test_cash_counts_per_account_currency_net() -> None:
    eur = account("EUR", [deposit(DAY, "100")])
    usd_up = account("USD", [deposit(DAY, "50")])
    usd_down = account("USD", [withdrawal(DAY, "25")])
    result = breakdown([eur, usd_up, usd_down], USD_RATE, "EUR", DAY)
    assert shares(result.by_currency) == [("EUR", d("100"), d("0.83333333")),
                                          ("USD", d("20"), d("0.16666667"))]  # fmt: skip
    assert shares(result.by_type) == [(CASH, d("120"), d("1"))]
    assert result.negative_cash == 0


def test_a_security_without_a_rate_is_left_out_and_makes_it_partial() -> None:
    chf, x = etf("CHF"), etf()
    eur = account("EUR", [buy(chf, DAY, "1", "10"), buy(x, DAY, "1", "50")], [chf, x],
                  {chf: "11", x: "60"})  # fmt: skip
    result = breakdown([eur], USD_RATE, "EUR", DAY)
    assert result.partial and result.unavailable_currencies == ()
    assert [s.key for s in result.by_asset] == [x.id]


def test_an_account_without_a_rate_is_left_out_an_empty_one_is_ignored() -> None:
    x = etf()
    eur = account("EUR", [buy(x, DAY, "1", "50")], [x], {x: "60"})
    gbp = account("GBP", [deposit(DAY, "10")])
    chf = account("CHF", [])
    result = breakdown([eur, gbp, chf], USD_RATE, "EUR", DAY)
    assert result.partial and result.unavailable_currencies == ("GBP",)
    assert shares(result.by_asset) == [(x.id, d("60"), d("1"))]
    alone = breakdown([eur, chf], USD_RATE, "EUR", DAY)
    assert not alone.partial and alone.unavailable_currencies == ()


def test_nothing_to_allocate() -> None:
    result = breakdown([account("EUR", [])], USD_RATE, "EUR", DAY)
    assert (result.by_type, result.by_currency, result.by_asset) == ((), (), ())
    assert result.income_by_month[-1].amounts.gross == 0 and len(result.income_by_month) == 12
    assert result.income_by_year == ()
    assert not result.partial


def test_income_by_month_covers_the_last_12_months_and_only_dividends_and_coupons() -> None:
    x, z = etf(), bond()
    eur = account(
        "EUR",
        [
            deposit(date(2024, 5, 2), "10000"),
            buy(x, date(2024, 5, 2), "10", "1000"),
            buy(z, date(2024, 5, 2), "2000", "1960", accrued="12.50"),  # accrued: not in it
            dividend(x, date(2024, 6, 3), "7.00", taxes="1.82"),
            coupon(z, date(2025, 10, 31), "30.00", taxes="3.75"),  # before the 12 months
            coupon(z, date(2025, 11, 2), "30.00", taxes="3.75"),
            dividend(x, date(2026, 10, 1), "9.00", taxes="-0.50"),  # a tax credit
        ],
        [x, z],
        {x: "100", z: "98"},
    )
    result = breakdown([eur], USD_RATE, "EUR", DAY)
    months = [(m.year, m.month) for m in result.income_by_month]
    assert months[0] == (2025, 11) and months[-1] == (2026, 10) and len(months) == 12
    by_month = {(m.year, m.month): m.amounts for m in result.income_by_month}
    assert (by_month[2025, 11].gross, by_month[2025, 11].taxes, by_month[2025, 11].net) == (
        d("30.00"), d("3.75"), d("26.25"))  # fmt: skip
    assert (by_month[2026, 10].gross, by_month[2026, 10].net) == (d("9.00"), d("9.50"))
    assert by_month[2026, 5].gross == 0
    years = {y.year: (y.amounts.gross, y.amounts.taxes) for y in result.income_by_year}
    assert years == {2024: (d("7.00"), d("1.82")), 2025: (d("60.00"), d("7.50")),
                     2026: (d("9.00"), d("-0.50"))}  # fmt: skip
    total = result.income_total
    assert (total.gross, total.taxes, total.net) == (d("76.00"), d("8.82"), d("67.18"))


def test_income_of_each_account_is_converted_at_the_latest_rate() -> None:
    x, u = etf(), etf("USD")
    eur = account("EUR", [buy(x, DAY, "1", "50"), dividend(x, date(2026, 3, 2), "10", taxes="2")],
                  [x], {x: "60"})  # fmt: skip
    usd = account("USD", [buy(u, DAY, "1", "50"), dividend(u, date(2026, 3, 9), "25", taxes="5")],
                  [u], {u: "60"})  # fmt: skip
    result = breakdown([eur, usd], USD_RATE, "EUR", DAY)
    march = next(m for m in result.income_by_month if (m.year, m.month) == (2026, 3))
    assert (march.amounts.gross, march.amounts.taxes) == (d("30"), d("6"))  # 10 + 25 / 1.25
    in_dollars = breakdown([usd], USD_RATE, "USD", DAY)  # one account: its own currency
    assert in_dollars.income_total.gross == d("25")


def test_income_after_the_day_is_not_counted() -> None:
    """The service's day is never before the last transaction; the engine checks it anyway."""
    x = etf()
    later = dataclasses.replace(dividend(x, date(2026, 11, 2), "9.00"), sequence=99)
    eur = account("EUR", [buy(x, DAY, "1", "50")], [x], {x: "60"})
    eur = dataclasses.replace(eur, transactions=(*eur.transactions, later))
    result = breakdown([eur], USD_RATE, "EUR", DAY)
    assert result.income_total.gross == 0 and result.income_by_year == ()


def test_no_asset_type_is_called_like_the_cash_category() -> None:
    assert CASH not in {t.value for t in AssetType}
