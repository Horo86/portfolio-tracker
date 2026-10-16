"""ORM rows <-> engine values. The engine stays free of database imports (ADR-0010)."""

from portfolio_tracker.db import models
from portfolio_tracker.engine import model as engine

# Columns shared one-to-one by the ORM row and the engine transaction.
_SAME_FIELDS = (
    "id",
    "trade_date",
    "trade_time",
    "value_date",
    "sequence",
    "asset_id",
    "quantity",
    "unit_price",
    "gross_amount",
    "fees",
    "accrued_interest_paid",
    "accrued_interest_received",
    "taxes",
    "total_amount",
    "affects_cash",
)


def to_engine_asset(row: models.Asset) -> engine.Asset:
    return engine.Asset(
        id=row.id,
        asset_type=engine.AssetType(row.asset_type),
        quote_currency=row.quote_currency,
        quote_unit=engine.QuoteUnit(row.quote_unit),
    )


def to_engine_transaction(row: models.Transaction) -> engine.Transaction:
    split = None
    if row.split_new is not None or row.split_old is not None:
        split = engine.SplitRatio(new=row.split_new, old=row.split_old)
    return engine.Transaction(
        type=engine.TransactionType(row.type),
        split_ratio=split,
        **{name: getattr(row, name) for name in _SAME_FIELDS},
    )


def copy_to_row(tx: engine.Transaction, row: models.Transaction) -> models.Transaction:
    """Write the engine fields of `tx` onto `row` (account, notes, refs are set by the caller)."""
    for name in _SAME_FIELDS:
        setattr(row, name, getattr(tx, name))
    row.type = tx.type.value
    row.split_new = tx.split_ratio.new if tx.split_ratio else None
    row.split_old = tx.split_ratio.old if tx.split_ratio else None
    return row
