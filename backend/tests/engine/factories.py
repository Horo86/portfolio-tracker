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


def opt(value: str | None) -> Decimal | None:
    """An optional amount: None means "not given" (counts as 0, D-62)."""
    return None if value is None else Decimal(value)


def _z(value: str | None) -> Decimal:
    return Decimal(0) if value is None else Decimal(value)


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
    fees: str | None = None,
    accrued: str | None = None,
    taxes: str | None = None,
    *,
    affects_cash: bool = True,
    at: time | None = None,
    **extra: object,
) -> Transaction:
    total = d(gross) + _z(fees) + _z(accrued) + _z(taxes)
    return _tx(
        TransactionType.BUY,
        on,
        at,
        asset_id=asset.id,
        quantity=d(quantity),
        gross_amount=d(gross),
        fees=opt(fees),
        accrued_interest_paid=opt(accrued),
        taxes=opt(taxes),
        total_amount=total,
        affects_cash=affects_cash,
        **extra,
    )


def sell(
    asset: Asset,
    on: date,
    quantity: str,
    gross: str,
    fees: str | None = None,
    accrued: str | None = None,
    taxes: str | None = None,
    *,
    affects_cash: bool = True,
    at: time | None = None,
    **extra: object,
) -> Transaction:
    total = d(gross) + _z(accrued) - _z(fees) - _z(taxes)
    return _tx(
        TransactionType.SELL,
        on,
        at,
        asset_id=asset.id,
        quantity=d(quantity),
        gross_amount=d(gross),
        fees=opt(fees),
        accrued_interest_received=opt(accrued),
        taxes=opt(taxes),
        total_amount=total,
        affects_cash=affects_cash,
        **extra,
    )


def income(
    type_: TransactionType,
    asset: Asset,
    on: date,
    gross: str,
    taxes: str | None = None,
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
        taxes=opt(taxes),
        total_amount=d(gross) - _z(taxes),
        affects_cash=affects_cash,
        **extra,
    )


def dividend(
    asset: Asset, on: date, gross: str, taxes: str | None = None, **extra: object
) -> Transaction:
    return income(TransactionType.DIVIDEND, asset, on, gross, taxes, **extra)  # type: ignore[arg-type]


def coupon(
    asset: Asset, on: date, gross: str, taxes: str | None = None, **extra: object
) -> Transaction:
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
