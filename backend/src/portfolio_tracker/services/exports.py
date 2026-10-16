"""The app CSV export (`docs/import/app-csv.md` "Export", D-38, D-107).

Every transaction of the user, or of one account, with every column of the format: what the
app CSV import needs to rebuild the same ledger (ids, account and asset definitions, original
broker types in `metadata`). Accounts by name, then each in replay order (domain-rules §4).
"""

from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import not_found
from portfolio_tracker.db import models
from portfolio_tracker.engine.model import AssetType, QuoteUnit, TransactionType
from portfolio_tracker.engine.ordering import replay_order
from portfolio_tracker.importers.app_csv import CsvRecord, write_csv
from portfolio_tracker.importers.base import AccountRef, AssetRef
from portfolio_tracker.repositories import accounts, assets, mapping
from portfolio_tracker.repositories import transactions as txs


def export_csv(db: Session, owner_id: UUID, account_id: UUID | None = None) -> str:
    """The CSV text; 404 when `account_id` is not one of the user's accounts."""
    if account_id is not None:
        account = accounts.get_account(db, owner_id, account_id)
        if account is None:
            raise not_found("account")
        chosen = [account]
    else:
        chosen = sorted(accounts.list_accounts(db, owner_id), key=lambda a: a.name)
    records = []
    for account in chosen:
        rows = {r.id: r for r in txs.account_ledger(db, owner_id, account.id)}
        used = assets.assets_by_id(db, {r.asset_id for r in rows.values() if r.asset_id})
        ref = AccountRef(name=account.name, currency=account.currency, provider=account.provider)
        ordered = replay_order(mapping.to_engine_transaction(r) for r in rows.values())
        records += [_record(rows[tx.id], ref, used) for tx in ordered]
    return write_csv(records)


def _record(row: models.Transaction, account: AccountRef, used: dict) -> CsvRecord:
    tx = mapping.to_engine_transaction(row)
    asset = used.get(row.asset_id) if row.asset_id else None
    return CsvRecord(
        account=account,
        type=TransactionType(row.type),
        trade_date=row.trade_date,
        id=row.id,
        trade_time=row.trade_time,
        value_date=row.value_date,
        asset=_asset(asset) if asset is not None else None,
        quantity=row.quantity,
        unit_price=row.unit_price,
        gross_amount=row.gross_amount,
        fees=row.fees,
        accrued_interest_paid=row.accrued_interest_paid,
        accrued_interest_received=row.accrued_interest_received,
        taxes=row.taxes,
        total_amount=row.total_amount,
        split_ratio=tx.split_ratio,
        affects_cash=row.affects_cash,  # written only where the type has the flag
        external_ref=row.external_ref,
        notes=row.notes,
        metadata=row.meta,
    )


def _asset(asset: models.Asset) -> AssetRef:
    return AssetRef(
        isin=asset.isin,
        ticker=asset.ticker,
        name=asset.name,
        asset_type=AssetType(asset.asset_type),
        quote_currency=asset.quote_currency,
        quote_unit=QuoteUnit(asset.quote_unit),
    )
