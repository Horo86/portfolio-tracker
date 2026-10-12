"""domain-rules.md §16: every worked example is an acceptance test (CLAUDE.md hard rule 4)."""

from datetime import date, time

import pytest
from factories import bond, buy, coupon, d, dividend, etf, sell, split

from portfolio_tracker.engine.ledger import NegativeQuantityError, replay
from portfolio_tracker.engine.metrics import summarize
from portfolio_tracker.engine.model import Asset, Transaction

JAN, FEB, MAR, APR = date(2026, 1, 15), date(2026, 2, 16), date(2026, 3, 16), date(2026, 4, 15)


def test_e1_average_cost() -> None:
    a = etf()
    first_buy = buy(a, JAN, "10", "1000.00", fees="5.00")
    ledger = replay([first_buy])
    assert ledger.positions[a.id].cost_basis == d("1005.00")
    assert ledger.positions[a.id].pmc == d("100.50")

    the_sell = sell(a, FEB, "4", "480.00", fees="2.00")
    ledger = replay([first_buy, the_sell])
    pos = ledger.positions[a.id]
    assert pos.cost_removed == d("402.00")
    assert pos.realized_gross == d("76.00")
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (d("6"), d("603.00"), d("100.50"))

    ledger = replay([first_buy, the_sell, buy(a, MAR, "4", "440.00", fees="1.00")])
    pos = ledger.positions[a.id]
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (d("10"), d("1044.00"), d("104.40"))


def test_e2_splits() -> None:
    a = etf()
    txs = [buy(a, JAN, "10", "1000"), split(a, FEB, "2", "1")]
    pos = replay(txs).positions[a.id]
    assert (pos.quantity, pos.pmc, pos.cost_basis) == (d("20"), d("50"), d("1000"))

    ledger = replay([*txs, split(a, MAR, "1", "10")])
    pos = ledger.positions[a.id]
    assert (pos.quantity, pos.pmc, pos.cost_basis) == (d("2"), d("500"), d("1000"))
    assert pos.realized_gross == 0
    assert ledger.cash == d("-1000")  # only the BUY moved cash


def test_e3_gross_vs_net_realized_and_income() -> None:
    a = etf()
    pos = replay(
        [
            buy(a, JAN, "10", "1000.00"),
            sell(a, FEB, "4", "480.00", taxes="20.80"),
            dividend(a, MAR, "20.00", taxes="5.20"),
        ]
    ).positions[a.id]
    assert pos.cost_removed == d("400.00")
    assert (pos.realized_gross, pos.realized_taxes, pos.realized_net) == (
        d("80.00"),
        d("20.80"),
        d("59.20"),
    )
    assert (pos.realized_pct_gross, pos.realized_pct_net) == (d("0.20"), d("0.148"))
    assert (pos.income_gross, pos.income_taxes, pos.income_net) == (
        d("20.00"),
        d("5.20"),
        d("14.80"),
    )
    assert pos.taxes == d("26.00")


def _e4_bond_ledger() -> tuple[Asset, list[Transaction]]:
    b = bond()
    txs = [
        buy(b, JAN, "5000", "4875.00", fees="5.00", accrued="12.34", taxes="-2.44"),
        coupon(b, FEB, "50.00", taxes="6.25"),
    ]
    return b, txs


def test_e4_bond_bought_on_the_secondary_market() -> None:
    b, txs = _e4_bond_ledger()
    assert txs[0].total_amount == d("4889.90")

    ledger = replay(txs[:1])
    pos = ledger.positions[b.id]
    assert pos.cost_basis == d("4880.00")
    assert pos.pmc * 100 == d("97.60")  # bond PMC shown as % of nominal
    assert ledger.cash == d("-4889.90")

    ledger = replay(txs)
    pos = ledger.positions[b.id]
    assert ledger.cash == d("-4889.90") + d("43.75")
    assert (pos.income_gross, pos.income_taxes, pos.income_net) == (
        d("37.66"),
        d("3.81"),
        d("33.85"),
    )
    assert pos.cost_basis == d("4880.00")  # accrued interest and BUY taxes are not cost


def test_e9_bond_sold_on_the_secondary_market() -> None:
    b, txs = _e4_bond_ledger()
    the_sell = sell(b, MAR, "2000", "1980.00", fees="3.00", accrued="8.00", taxes="3.13")
    assert the_sell.total_amount == d("1981.87")

    ledger = replay([*txs, the_sell])
    pos = ledger.positions[b.id]
    assert pos.cost_removed == d("1952.00")
    assert pos.realized_gross == d("25.00")
    assert pos.realized_taxes == d("3.13")
    assert pos.realized_net == d("21.87")
    assert pos.realized_pct_gross == d("0.01280738")  # 1.28%
    assert pos.realized_pct_net == d("0.01120389")  # 1.12%
    assert (pos.quantity, pos.cost_basis, pos.pmc * 100) == (d("3000"), d("2928.00"), d("97.60"))
    assert (pos.income_gross, pos.income_taxes, pos.income_net) == (
        d("45.66"),
        d("3.81"),
        d("41.85"),
    )
    assert ledger.cash == d("-2864.28")


def test_account_summary_adds_up_positions() -> None:
    b, txs = _e4_bond_ledger()
    a = etf()
    ledger = replay(
        [
            *txs,
            sell(b, MAR, "2000", "1980.00", fees="3.00", accrued="8.00", taxes="3.13"),
            buy(a, JAN, "10", "1000.00"),
            sell(a, FEB, "4", "480.00", taxes="20.80"),
            dividend(a, MAR, "20.00", taxes="5.20"),
        ]
    )
    totals = summarize(ledger)
    assert totals.cost_removed == d("2352.00")
    assert totals.realized_gross == d("105.00")
    assert totals.realized_taxes == d("23.93")
    assert totals.realized_net == d("81.07")
    assert totals.realized_pct_gross == d("0.04464286")
    assert totals.income_gross == d("65.66")
    assert totals.income_taxes == d("9.01")
    assert totals.income_net == d("56.65")
    assert totals.invested_capital == d("2928.00") + d("600.00")


def test_e5_existing_holding() -> None:
    a = etf()
    existing = buy(a, JAN, "50", "400.00", affects_cash=False)
    ledger = replay([existing])
    pos = ledger.positions[a.id]
    assert (ledger.cash, pos.cost_basis, pos.pmc) == (0, d("400.00"), d("8.00"))

    ledger = replay([existing, buy(a, FEB, "10", "90.00")])
    pos = ledger.positions[a.id]
    assert ledger.cash == d("-90.00")
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (d("60"), d("490.00"), d("8.16666667"))


def test_e7_same_day_ordering() -> None:
    a = etf()
    replay([buy(a, JAN, "10", "1000"), sell(a, JAN, "10", "1000")])  # BUY then SELL: valid

    timed_buy = buy(a, JAN, "10", "1000", at=time(10, 0))
    untimed_sell = sell(a, JAN, "10", "1000")
    with pytest.raises(NegativeQuantityError) as exc:
        replay([untimed_sell, timed_buy])
    assert exc.value.transaction_id == untimed_sell.id
    assert exc.value.quantity == d("-10")

    later_sell = sell(a, JAN, "10", "1000", at=time(11, 0))
    assert replay([timed_buy, later_sell]).positions[a.id].quantity == 0


def test_e8_rounding_without_drift() -> None:
    a = etf()
    first = buy(a, JAN, "3", "100.00")
    pos = replay([first]).positions[a.id]
    assert pos.pmc == d("33.33333333")

    one = sell(a, FEB, "1", "40.00")
    pos = replay([first, one]).positions[a.id]
    assert pos.cost_removed == d("33.33333333")
    assert pos.realized_gross == d("6.66666667")
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (d("2"), d("66.66666667"), d("33.33333334"))

    pos = replay([first, one, sell(a, MAR, "2", "80.00")]).positions[a.id]
    assert pos.realized_gross == d("20.00000000")
    assert pos.cost_removed == d("100.00000000")
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (0, 0, None)


def test_e10_moving_a_holding_between_accounts() -> None:
    a = etf()
    account_a = [
        buy(a, JAN, "10", "1000.00", fees="5.00"),
        sell(a, FEB, "4", "402.00", affects_cash=False),
    ]
    ledger_a = replay(account_a)
    pos = ledger_a.positions[a.id]
    assert pos.cost_removed == d("402.00")
    assert pos.realized_gross == 0
    assert (pos.quantity, pos.cost_basis, pos.pmc) == (d("6"), d("603.00"), d("100.50"))
    assert ledger_a.cash == d("-1005.00")

    ledger_b = replay([buy(a, FEB, "4", "402.00", affects_cash=False)])
    assert ledger_b.positions[a.id].pmc == d("100.50")
    assert ledger_b.cash == 0
