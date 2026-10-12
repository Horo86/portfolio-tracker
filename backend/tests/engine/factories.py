"""Small builders for engine tests. Invented data only."""

from datetime import date, time
from decimal import Decimal
from itertools import count
from uuid import UUID, uuid4

from portfolio_tracker.engine.model import (
    Asset,
    AssetType,
    QuoteUnit,
    SplitRatio,
    Transaction,
    TransactionType,
)

_sequence = count(1)


def d(value: str | int) -> Decimal:
    return Decimal(value)


def etf(quote_currency: str = "EUR") -> Asset:
    return Asset(uuid4(), AssetType.ETF, quote_currency, QuoteUnit.CURRENCY)


def stock_gbx() -> Asset:
    return Asset(uuid4(), AssetType.STOCK, "GBP", QuoteUnit.MINOR_CURRENCY_UNIT)


def bond(quote_currency: str = "EUR") -> Asset:
    return Asset(uuid4(), AssetType.BOND, quote_currency, QuoteUnit.PERCENT_OF_NOMINAL)


def _tx(type_: TransactionType, on: date, at: time | None, **fields: object) -> Transaction:
    return Transaction(
        id=fields.pop("id", None) or uuid4(),  # type: ignore[arg-type]
        type=type_,
        trade_date=on,
        trade_time=at,
        sequence=fields.pop("sequence", None) or next(_sequence),  # type: ignore[arg-type]
        **fields,  # type: ignore[arg-type]
    )


def buy(
    asset: Asset,
    on: date,
    quantity: str,
    gross: str,
    fees: str = "0",
    accrued: str = "0",
    taxes: str = "0",
    *,
    affects_cash: bool = True,
    at: time | None = None,
    **extra: object,
) -> Transaction:
    total = d(gross) + d(fees) + d(accrued) + d(taxes)
    return _tx(
        TransactionType.BUY,
        on,
        at,
        asset_id=asset.id,
        quantity=d(quantity),
        gross_amount=d(gross),
        fees=d(fees),
        accrued_interest_paid=d(accrued),
        taxes=d(taxes),
        total_amount=total,
        affects_cash=affects_cash,
        **extra,
    )


def sell(
    asset: Asset,
    on: date,
    quantity: str,
    gross: str,
    fees: str = "0",
    accrued: str = "0",
    taxes: str = "0",
    *,
    affects_cash: bool = True,
    at: time | None = None,
    **extra: object,
) -> Transaction:
    total = d(gross) + d(accrued) - d(fees) - d(taxes)
    return _tx(
        TransactionType.SELL,
        on,
        at,
        asset_id=asset.id,
        quantity=d(quantity),
        gross_amount=d(gross),
        fees=d(fees),
        accrued_interest_received=d(accrued),
        taxes=d(taxes),
        total_amount=total,
        affects_cash=affects_cash,
        **extra,
    )


def income(
    type_: TransactionType,
    asset: Asset,
    on: date,
    gross: str,
    taxes: str = "0",
    *,
    affects_cash: bool = True,
    **extra: object,
) -> Transaction:
    return _tx(
        type_,
        on,
        None,
        asset_id=asset.id,
        gross_amount=d(gross),
        taxes=d(taxes),
        total_amount=d(gross) - d(taxes),
        affects_cash=affects_cash,
        **extra,
    )


def dividend(asset: Asset, on: date, gross: str, taxes: str = "0", **extra: object) -> Transaction:
    return income(TransactionType.DIVIDEND, asset, on, gross, taxes, **extra)  # type: ignore[arg-type]


def coupon(asset: Asset, on: date, gross: str, taxes: str = "0", **extra: object) -> Transaction:
    return income(TransactionType.COUPON, asset, on, gross, taxes, **extra)  # type: ignore[arg-type]


def deposit(on: date, amount: str, *, at: time | None = None, **extra: object) -> Transaction:
    return _tx(TransactionType.DEPOSIT, on, at, total_amount=d(amount), **extra)


def withdrawal(on: date, amount: str, *, at: time | None = None, **extra: object) -> Transaction:
    return _tx(TransactionType.WITHDRAWAL, on, at, total_amount=d(amount), **extra)


def split(asset: Asset, on: date, new: str, old: str, **extra: object) -> Transaction:
    return _tx(
        TransactionType.STOCK_SPLIT,
        on,
        None,
        asset_id=asset.id,
        split_ratio=SplitRatio(d(new), d(old)),
        **extra,
    )


def assets_by_id(*assets: Asset) -> dict[UUID, Asset]:
    return {asset.id: asset for asset in assets}
