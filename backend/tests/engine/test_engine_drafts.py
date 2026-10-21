"""Amounts completed for a draft transaction (domain-rules §2, D-139). Invented data only."""

import dataclasses
from datetime import date
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from factories import bond, d, etf, stock_gbx

from portfolio_tracker.engine.drafts import complete_amounts
from portfolio_tracker.engine.model import Asset, SplitRatio, Transaction, TransactionType
from portfolio_tracker.engine.validation import validate_transaction

T = TransactionType
DAY = date(2026, 3, 2)
ETF = etf()
BOND = bond()
GBX = stock_gbx()


def draft(type_: TransactionType, asset: Asset | None = None, **amounts: str) -> Transaction:
    """A transaction as the form sends it: amounts not typed are None."""
    return Transaction(
        id=uuid4(),
        type=type_,
        trade_date=DAY,
        sequence=1,
        asset_id=asset.id if asset else None,
        **{name: d(value) for name, value in amounts.items()},
    )


def test_buy_gross_from_price_and_total_from_invariant() -> None:
    done = complete_amounts(draft(T.BUY, ETF, quantity="15", unit_price="101.80", fees="3.00"),
                            ETF, "EUR")  # fmt: skip
    assert done.transaction.gross_amount == d("1527.00")
    assert done.transaction.total_amount == d("1530.00")
    assert done.derived == {"gross_amount", "total_amount"}
    assert validate_transaction(done.transaction, ETF) == []


def test_minor_currency_unit_divides_the_price_by_100() -> None:
    done = complete_amounts(draft(T.BUY, GBX, quantity="100", unit_price="1234.5"), GBX, "GBP")
    assert done.transaction.gross_amount == d("1234.5")
    assert done.transaction.total_amount == d("1234.5")


def test_bond_buy_percent_of_nominal_e4() -> None:
    tx = draft(T.BUY, BOND, quantity="5000", unit_price="97.50", fees="5.00",
               accrued_interest_paid="12.34", taxes="-2.44")  # fmt: skip
    done = complete_amounts(tx, BOND, "EUR")
    assert done.transaction.gross_amount == d("4875.00")
    assert done.transaction.total_amount == d("4889.90")  # §16 E4
    assert validate_transaction(done.transaction, BOND) == []


def test_bond_sell_e9() -> None:
    tx = draft(T.SELL, BOND, quantity="2000", unit_price="99.00", fees="3.00",
               accrued_interest_received="8.00", taxes="3.13")  # fmt: skip
    done = complete_amounts(tx, BOND, "EUR")
    assert done.transaction.gross_amount == d("1980.00")
    assert done.transaction.total_amount == d("1981.87")  # 1980 + 8 - 3 - 3.13


def test_derived_gross_is_exact_never_rounded_to_cents() -> None:
    done = complete_amounts(draft(T.BUY, ETF, quantity="0.12345678", unit_price="101.80"),
                            ETF, "EUR")  # fmt: skip
    assert done.transaction.gross_amount == d("12.567900204")
    assert done.transaction.total_amount == d("12.567900204")


def test_exact_whatever_the_callers_decimal_context() -> None:
    tx = draft(T.BUY, ETF, quantity="123456789.12345678", unit_price="98765.4321")
    with localcontext() as ctx:
        ctx.prec = 6
        done = complete_amounts(tx, ETF, "EUR")
    assert str(done.transaction.gross_amount) == "12193263123456.789122374638"  # 26 digits


def test_foreign_quote_currency_needs_the_brokers_gross() -> None:
    usd = etf("USD")
    done = complete_amounts(draft(T.BUY, usd, quantity="10", unit_price="140.20"), usd, "EUR")
    assert done.transaction.gross_amount is None
    assert done.transaction.total_amount is None
    assert done.derived == set()


def test_foreign_quote_currency_total_from_the_typed_gross() -> None:
    usd = etf("USD")
    tx = draft(T.BUY, usd, quantity="10", unit_price="140.20", gross_amount="1291.10",
               fees="3.00")  # fmt: skip
    done = complete_amounts(tx, usd, "EUR")
    assert done.transaction.gross_amount == d("1291.10")
    assert done.transaction.total_amount == d("1294.10")
    assert done.derived == {"total_amount"}


def test_typed_gross_is_kept_even_when_it_differs_from_quantity_times_price() -> None:
    tx = draft(T.SELL, ETF, quantity="3", unit_price="33.333", gross_amount="100.00", fees="1.00")
    done = complete_amounts(tx, ETF, "EUR")
    assert done.transaction.gross_amount == d("100.00")
    assert done.transaction.total_amount == d("99.00")
    assert done.derived == {"total_amount"}


def test_typed_total_is_never_changed() -> None:
    tx = draft(T.BUY, ETF, quantity="2", unit_price="10", total_amount="25.00")
    done = complete_amounts(tx, ETF, "EUR")
    assert done.transaction.gross_amount == d("20")
    assert done.transaction.total_amount == d("25.00")  # wrong, left for validation to reject
    assert done.derived == {"gross_amount"}
    assert ("total_amount", "invariant") in {
        (i.field, i.code) for i in validate_transaction(done.transaction, ETF)
    }


@pytest.mark.parametrize("missing", ["quantity", "unit_price"])
def test_no_gross_without_quantity_and_price(missing: str) -> None:
    amounts = {"quantity": "5", "unit_price": "10.00"}
    del amounts[missing]
    done = complete_amounts(draft(T.BUY, ETF, **amounts), ETF, "EUR")
    assert done.transaction.gross_amount is None
    assert done.transaction.total_amount is None
    assert done.derived == set()


def test_no_gross_without_the_asset() -> None:
    done = complete_amounts(draft(T.BUY, ETF, quantity="5", unit_price="10.00"), None, "EUR")
    assert done.transaction.gross_amount is None


@pytest.mark.parametrize(
    ("type_", "asset", "amounts", "total"),
    [
        (T.DIVIDEND, ETF, {"gross_amount": "20.00", "taxes": "5.20"}, "14.80"),  # E3
        (T.DIVIDEND, ETF, {"gross_amount": "20.00", "quantity": "40"}, "20.00"),
        (T.COUPON, BOND, {"gross_amount": "50.00", "taxes": "6.25"}, "43.75"),  # E4
    ],
)
def test_income_total_from_gross_and_taxes(type_, asset, amounts, total) -> None:
    done = complete_amounts(draft(type_, asset, **amounts), asset, "EUR")
    assert done.transaction.total_amount == d(total)
    assert done.derived == {"total_amount"}


def test_dividend_gross_is_never_derived_from_a_quantity() -> None:
    done = complete_amounts(draft(T.DIVIDEND, ETF, quantity="40"), ETF, "EUR")
    assert done.transaction.gross_amount is None
    assert done.transaction.total_amount is None


@pytest.mark.parametrize("type_", [T.DEPOSIT, T.WITHDRAWAL])
def test_cash_movements_are_typed_never_derived(type_: TransactionType) -> None:
    empty = draft(type_)
    assert complete_amounts(empty, None, "EUR").transaction == empty
    typed = draft(type_, total_amount="500.00")
    done = complete_amounts(typed, None, "EUR")
    assert done.transaction == typed
    assert done.derived == set()


def test_split_is_unchanged() -> None:
    tx = dataclasses.replace(draft(T.STOCK_SPLIT, ETF), split_ratio=SplitRatio(d(2), d(1)))
    done = complete_amounts(tx, ETF, "EUR")
    assert done.transaction == tx
    assert done.derived == set()


def test_other_fields_are_untouched() -> None:
    tx = draft(T.BUY, ETF, quantity="1", unit_price="9.99")
    done = complete_amounts(tx, ETF, "EUR").transaction
    for field in ("id", "type", "trade_date", "sequence", "asset_id", "quantity", "unit_price",
                  "fees", "taxes", "affects_cash"):  # fmt: skip
        assert getattr(done, field) == getattr(tx, field)
    assert isinstance(done.gross_amount, Decimal)
