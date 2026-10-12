import dataclasses
from datetime import date
from decimal import Decimal

import pytest
from factories import bond, buy, coupon, d, deposit, dividend, etf, sell, split, withdrawal

from portfolio_tracker.engine.model import Asset, SplitRatio, Transaction
from portfolio_tracker.engine.validation import FieldIssue, validate_transaction

DAY = date(2026, 3, 2)
ETF = etf()
BOND = bond()


def codes(tx: Transaction, asset: Asset | None) -> set[tuple[str, str]]:
    return {(issue.field, issue.code) for issue in validate_transaction(tx, asset)}


# Valid transactions from the §16 examples ---------------------------------


@pytest.mark.parametrize(
    ("tx", "asset"),
    [
        (buy(ETF, DAY, "10", "1000.00", fees="5.00"), ETF),  # E1
        (sell(ETF, DAY, "4", "480.00", taxes="20.80"), ETF),  # E3
        (dividend(ETF, DAY, "20.00", taxes="5.20"), ETF),  # E3
        (buy(BOND, DAY, "5000", "4875.00", "5.00", "12.34", "-2.44"), BOND),  # E4
        (coupon(BOND, DAY, "50.00", taxes="6.25"), BOND),  # E4
        (sell(BOND, DAY, "2000", "1980.00", "3.00", "8.00", "3.13"), BOND),  # E9
        (buy(ETF, DAY, "50", "400.00", affects_cash=False), ETF),  # E5
        (split(ETF, DAY, "1", "10"), ETF),  # E2
        (deposit(DAY, "1000"), None),
        (withdrawal(DAY, "2.00"), None),
    ],
)
def test_section16_transactions_are_valid(tx: Transaction, asset: Asset | None) -> None:
    assert validate_transaction(tx, asset) == []


def test_e4_total_matches_exactly() -> None:
    tx = buy(BOND, DAY, "5000", "4875.00", "5.00", "12.34", "-2.44")
    assert tx.total_amount == Decimal("4889.90")


# Invariants (D-60: exact comparison, no tolerance) ---------------------------


def test_invariant_accepts_same_value_with_more_zeros() -> None:
    tx = buy(BOND, DAY, "5000", "4875.00", "5.00", "12.34", "-2.44")
    tx = dataclasses.replace(tx, total_amount=d("4889.900"))
    assert validate_transaction(tx, BOND) == []


@pytest.mark.parametrize(
    "tx",
    [
        dataclasses.replace(buy(ETF, DAY, "10", "1000.00", "5.00"), total_amount=d("1005.01")),
        dataclasses.replace(sell(ETF, DAY, "4", "480.00", "2.00"), total_amount=d("480.00")),
        dataclasses.replace(dividend(ETF, DAY, "20.00", "5.20"), total_amount=d("20.00")),
    ],
)
def test_invariant_violation_is_reported(tx: Transaction) -> None:
    issues = validate_transaction(tx, ETF)
    assert issues == [FieldIssue("total_amount", "invariant")]


# Required fields and signs ---------------------------------------------------


def test_buy_requires_positive_quantity_and_total() -> None:
    tx = dataclasses.replace(buy(ETF, DAY, "10", "100"), quantity=d("0"), total_amount=None)
    assert codes(tx, ETF) == {("quantity", "must_be_positive"), ("total_amount", "required")}


def test_negative_fees_and_gross_are_rejected() -> None:
    tx = dataclasses.replace(sell(ETF, DAY, "1", "10"), fees=d("-1"), gross_amount=d("-10"))
    assert {("fees", "must_not_be_negative"), ("gross_amount", "must_not_be_negative")} <= codes(
        tx, ETF
    )


def test_income_requires_positive_gross_and_non_negative_taxes() -> None:
    tx = dataclasses.replace(dividend(ETF, DAY, "10"), gross_amount=d("0"), taxes=d("-1"))
    assert {("gross_amount", "must_be_positive"), ("taxes", "must_not_be_negative")} <= codes(
        tx, ETF
    )


@pytest.mark.parametrize("make", [deposit, withdrawal])
def test_cash_movements_require_positive_amount(make) -> None:  # type: ignore[no-untyped-def]
    assert codes(make(DAY, "0"), None) == {("total_amount", "must_be_positive")}


# Asset rules --------------------------------------------------------------


def test_trade_without_asset_is_rejected() -> None:
    tx = dataclasses.replace(buy(ETF, DAY, "1", "10"), asset_id=None)
    assert codes(tx, None) == {("asset_id", "required")}


def test_asset_must_match_the_transaction() -> None:
    assert codes(buy(ETF, DAY, "1", "10"), BOND) == {("asset_id", "mismatch")}


def test_deposit_with_asset_is_rejected() -> None:
    tx = dataclasses.replace(deposit(DAY, "10"), asset_id=ETF.id)
    assert codes(tx, ETF) == {("asset_id", "not_allowed")}


def test_coupon_requires_a_bond() -> None:
    assert codes(coupon(ETF, DAY, "10"), ETF) == {("asset_id", "must_be_bond")}


# Bond-only fields (D-42, D-43) -------------------------------------------------


def test_accrued_interest_and_buy_taxes_are_bond_only() -> None:
    tx = buy(ETF, DAY, "10", "100", accrued="1", taxes="-1")
    assert codes(tx, ETF) == {
        ("accrued_interest_paid", "bond_only"),
        ("taxes", "bond_only"),
    }
    assert codes(sell(ETF, DAY, "10", "100", accrued="1"), ETF) == {
        ("accrued_interest_received", "bond_only")
    }


def test_sell_taxes_are_allowed_on_any_asset() -> None:
    assert validate_transaction(sell(ETF, DAY, "1", "10", taxes="-0.50"), ETF) == []


def test_accrued_interest_on_the_wrong_side_is_rejected() -> None:
    tx = dataclasses.replace(buy(BOND, DAY, "100", "99"), accrued_interest_received=d("1"))
    assert codes(tx, BOND) == {("accrued_interest_received", "not_allowed")}


def test_negative_accrued_interest_is_rejected() -> None:
    tx = buy(BOND, DAY, "100", "99", accrued="-1")
    assert codes(tx, BOND) == {("accrued_interest_paid", "must_not_be_negative")}


# Fields that do not belong to the type -------------------------------------------


def test_affects_cash_false_only_on_trades_and_income() -> None:
    tx = dataclasses.replace(deposit(DAY, "10"), affects_cash=False)
    assert codes(tx, None) == {("affects_cash", "not_allowed")}


def test_split_needs_positive_ratio_and_no_amounts() -> None:
    tx = dataclasses.replace(
        split(ETF, DAY, "2", "1"),
        split_ratio=SplitRatio(d("0"), d("1")),
        gross_amount=d("1"),
    )
    assert codes(tx, ETF) == {("split_ratio", "must_be_positive"), ("gross_amount", "not_allowed")}
    missing = dataclasses.replace(split(ETF, DAY, "2", "1"), split_ratio=None)
    assert codes(missing, ETF) == {("split_ratio", "required")}


def test_split_ratio_not_allowed_on_buy() -> None:
    tx = dataclasses.replace(buy(ETF, DAY, "1", "10"), split_ratio=SplitRatio(d(2), d(1)))
    assert codes(tx, ETF) == {("split_ratio", "not_allowed")}


def test_dividend_may_carry_informational_quantity_but_not_fees() -> None:
    assert validate_transaction(dividend(ETF, DAY, "10", quantity=d("5")), ETF) == []
    tx = dataclasses.replace(dividend(ETF, DAY, "10"), fees=d("1"))
    assert codes(tx, ETF) == {("fees", "not_allowed")}
