import dataclasses
from datetime import date, time

import pytest
from factories import buy, d, deposit, dividend, etf, sell, split, withdrawal

from portfolio_tracker.engine.ledger import (
    LedgerWarning,
    NegativeQuantityError,
    SplitToZeroError,
    WarningCode,
    cost_removed_for,
    future_dated,
    replay,
)

D1, D2, D3, D4 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)


def test_empty_ledger() -> None:
    ledger = replay([])
    assert (ledger.positions, ledger.cash, ledger.warnings) == ({}, 0, ())


def test_cash_effects_by_type() -> None:
    a = etf()
    ledger = replay(
        [
            deposit(D1, "1000"),
            buy(a, D2, "10", "500", fees="1"),
            dividend(a, D3, "10", taxes="2.60"),
            sell(a, D4, "5", "300", fees="1", taxes="5"),
            withdrawal(D4, "100"),
        ]
    )
    assert ledger.cash == d("1000") - d("501") + d("7.40") + d("294") - d("100")
    assert ledger.warnings == ()


def test_overselling_names_the_first_conflicting_transaction() -> None:
    a = etf()
    first_bad = sell(a, D2, "6", "60")
    with pytest.raises(NegativeQuantityError) as exc:
        replay([buy(a, D1, "5", "50"), first_bad, sell(a, D3, "10", "100")])
    assert (exc.value.transaction_id, exc.value.asset_id) == (first_bad.id, a.id)
    assert exc.value.quantity == d("-1")
    assert exc.value.held == d("5")  # before the sale (D-139)


def test_negative_cash_warns_with_first_date_at_end_of_day() -> None:
    a = etf()
    ledger = replay(
        [
            buy(a, D1, "1", "100"),  # cash -100 at the end of D1
            deposit(D2, "200"),
            withdrawal(D3, "150"),  # -50 again, but only the first date is reported
        ]
    )
    assert ledger.warnings == (LedgerWarning(WarningCode.NEGATIVE_CASH, D1, None),)


def test_intraday_dip_recovered_the_same_day_does_not_warn() -> None:
    a = etf()
    # a timed deposit later the same day still counts for the end-of-day balance.
    ledger = replay([buy(a, D1, "1", "100"), deposit(D1, "100", at=time(18))])
    assert ledger.warnings == ()


def test_transactions_without_cash_effect_never_warn() -> None:
    a = etf()
    ledger = replay([buy(a, D1, "1", "100", affects_cash=False)])
    assert (ledger.cash, ledger.warnings) == (0, ())


def test_income_on_never_held_asset_warns_but_is_accepted() -> None:
    a = etf()
    div = dividend(a, D1, "10")
    ledger = replay([div])
    assert ledger.warnings == (LedgerWarning(WarningCode.INCOME_WITHOUT_HOLDING, D1, div.id),)
    assert ledger.cash == d("10")


def test_income_on_closed_position_does_not_warn() -> None:
    a = etf()
    ledger = replay([buy(a, D1, "1", "10"), sell(a, D2, "1", "10"), dividend(a, D3, "1")])
    assert WarningCode.INCOME_WITHOUT_HOLDING not in {w.code for w in ledger.warnings}
    assert ledger.positions[a.id].quantity == 0


def test_replay_as_of_a_date_ignores_later_transactions() -> None:
    a = etf()
    txs = [buy(a, D1, "2", "20"), sell(a, D3, "1", "15")]
    assert replay(txs, as_of=D2).positions[a.id].quantity == d("2")
    assert replay(txs, as_of=D3).positions[a.id].quantity == d("1")


def test_selling_everything_removes_the_exact_cost() -> None:
    a = etf()
    pos = replay([buy(a, D1, "3", "100.123456789"), sell(a, D2, "3", "90")]).positions[a.id]
    assert pos.cost_removed == d("100.123456789")
    assert pos.cost_basis == 0


def test_split_quantity_is_rounded_to_eight_decimals() -> None:
    a = etf()
    pos = replay([buy(a, D1, "10", "100"), split(a, D2, "1", "3")]).positions[a.id]
    assert pos.quantity == d("3.33333333")
    assert pos.cost_basis == d("100")


def test_split_that_rounds_a_held_quantity_to_zero_is_rejected() -> None:
    # Review scenario: 0.00000001 x 1/2 rounds (half-even) to 0 but the cost would stay (D-69).
    a = etf()
    the_split = split(a, D2, "1", "2")
    with pytest.raises(SplitToZeroError) as exc:
        replay([buy(a, D1, "0.00000001", "5"), the_split, buy(a, D3, "1", "10")])
    assert (exc.value.transaction_id, exc.value.asset_id) == (the_split.id, a.id)
    assert (exc.value.quantity, exc.value.held) == (d(0), d("0.00000001"))


def test_split_on_a_closed_or_never_held_position_is_fine() -> None:
    a, b = etf(), etf()
    ledger = replay(
        [
            buy(a, D1, "1", "10"),
            sell(a, D2, "1", "12"),
            split(a, D3, "1", "10"),
            split(b, D3, "1", "3"),
        ]
    )
    assert ledger.positions[a.id].quantity == 0
    assert ledger.positions[b.id].quantity == 0


def test_cost_removed_for_follows_d40_and_d63() -> None:
    assert cost_removed_for(d("100.00"), d("3"), d("1")) == d("33.33333333")  # E8
    assert cost_removed_for(d("100.123456789"), d("3"), d("3")) == d("100.123456789")
    for sold in ("0", "4"):
        with pytest.raises(ValueError):
            cost_removed_for(d("100"), d("3"), d(sold))


def test_replayed_positions_are_read_only() -> None:
    a = etf()
    ledger = replay([buy(a, D1, "1", "10")])
    with pytest.raises(dataclasses.FrozenInstanceError):
        ledger.positions[a.id].quantity = d("5")  # type: ignore[misc]
    with pytest.raises(TypeError):
        ledger.positions[a.id] = ledger.positions[a.id]  # type: ignore[index]


def test_future_dated_transactions_are_flagged_not_rejected() -> None:
    a = etf()
    today_tx, tomorrow_tx = deposit(D1, "100"), buy(a, D2, "1", "10")
    assert future_dated([today_tx], today=D1) == ()
    assert future_dated([tomorrow_tx, today_tx], today=D1) == (
        LedgerWarning(WarningCode.FUTURE_DATE, D2, tomorrow_tx.id),
    )
    assert future_dated([], today=D1) == ()
