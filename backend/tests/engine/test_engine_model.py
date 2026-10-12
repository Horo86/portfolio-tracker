import dataclasses
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from portfolio_tracker.engine.decimals import EngineDecimalError
from portfolio_tracker.engine.model import (
    Asset,
    AssetType,
    QuoteUnit,
    SplitRatio,
    Transaction,
    TransactionType,
)
from portfolio_tracker.engine.valuation import FxRates, PriceHistory


def test_enums_match_domain_names() -> None:
    assert [t.value for t in TransactionType] == [
        "BUY",
        "SELL",
        "DIVIDEND",
        "COUPON",
        "DEPOSIT",
        "WITHDRAWAL",
        "STOCK_SPLIT",
    ]
    assert [t.value for t in AssetType] == ["ETF", "STOCK", "BOND"]
    assert [u.value for u in QuoteUnit] == ["CURRENCY", "MINOR_CURRENCY_UNIT", "PERCENT_OF_NOMINAL"]


def test_transaction_defaults() -> None:
    tx = Transaction(
        id=uuid4(),
        type=TransactionType.DEPOSIT,
        trade_date=date(2026, 1, 2),
        sequence=1,
        total_amount=Decimal("100"),
    )

    assert tx.fees is None  # not given: counts as 0, rejected where not allowed (D-62)
    assert tx.taxes is None
    assert tx.accrued_interest_paid is None
    assert tx.accrued_interest_received is None
    assert tx.affects_cash is True
    assert tx.trade_time is None
    assert tx.asset_id is None


def test_values_are_immutable() -> None:
    asset = Asset(
        id=uuid4(),
        asset_type=AssetType.BOND,
        quote_currency="EUR",
        quote_unit=QuoteUnit.PERCENT_OF_NOMINAL,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        asset.quote_currency = "USD"  # type: ignore[misc]
    assert asset.is_bond


def test_split_ratio_keeps_new_and_old() -> None:
    ratio = SplitRatio(new=Decimal(1), old=Decimal(10))
    assert (ratio.new, ratio.old) == (Decimal(1), Decimal(10))


@pytest.mark.parametrize("bad", [1.5, 1, True, Decimal("NaN"), Decimal("Infinity")])
def test_transaction_rejects_non_decimal_amounts(bad: object) -> None:
    with pytest.raises(EngineDecimalError):
        Transaction(
            id=uuid4(),
            type=TransactionType.DEPOSIT,
            trade_date=date(2026, 1, 2),
            sequence=1,
            total_amount=bad,  # type: ignore[arg-type]
        )


def test_split_ratio_rejects_floats() -> None:
    with pytest.raises(EngineDecimalError):
        SplitRatio(new=2.0, old=Decimal(1))  # type: ignore[arg-type]


def test_price_and_fx_series_reject_floats() -> None:
    with pytest.raises(EngineDecimalError):
        PriceHistory({date(2026, 1, 2): 10.5})  # type: ignore[dict-item]
    with pytest.raises(EngineDecimalError):
        FxRates({"USD": {date(2026, 1, 2): 1.1}})  # type: ignore[dict-item]
