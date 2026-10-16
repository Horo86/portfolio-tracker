"""Transaction changes validated on the final state by the engine (domain-rules §4, D-06,
D-06b, D-70, D-82 answers of the M2 plan).

Every create, edit (also moving a transaction to another account of the same owner), bulk
edit and delete goes through `apply_change`: the affected account ledgers are loaded (owner
filtered), the engine validates the fields and replays each ledger, and the change is written
only if every affected ledger is accepted. Nothing is stored when it is rejected. It must run
inside the request's write transaction (BEGIN IMMEDIATE), so validation and persistence cannot
interleave with another change.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.db import models
from portfolio_tracker.engine import edits
from portfolio_tracker.engine import model as engine
from portfolio_tracker.engine.ledger import Ledger, LedgerWarning, future_dated, replay
from portfolio_tracker.repositories import accounts, assets, audit, mapping
from portfolio_tracker.repositories import transactions as txs


@dataclass(frozen=True, slots=True)
class TxData:
    """The writable content of a transaction (id, sequence and timestamps are the app's)."""

    account_id: UUID
    type: engine.TransactionType
    trade_date: date
    trade_time: time | None = None
    value_date: date | None = None
    asset_id: UUID | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    gross_amount: Decimal | None = None
    fees: Decimal | None = None
    accrued_interest_paid: Decimal | None = None
    accrued_interest_received: Decimal | None = None
    taxes: Decimal | None = None
    total_amount: Decimal | None = None
    split_ratio: engine.SplitRatio | None = None
    affects_cash: bool = True
    external_ref: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def of_row(cls, row: models.Transaction) -> "TxData":
        tx = mapping.to_engine_transaction(row)
        return cls(
            account_id=row.account_id,
            **{name: getattr(tx, name) for name in _ENGINE_FIELDS},
            external_ref=row.external_ref,
            notes=row.notes,
            metadata=row.meta,
        )

    def engine_tx(self, tx_id: UUID, sequence: int) -> engine.Transaction:
        return engine.Transaction(
            id=tx_id, sequence=sequence, **{name: getattr(self, name) for name in _ENGINE_FIELDS}
        )


_ENGINE_FIELDS = (
    "type",
    "trade_date",
    "trade_time",
    "value_date",
    "asset_id",
    "quantity",
    "unit_price",
    "gross_amount",
    "fees",
    "accrued_interest_paid",
    "accrued_interest_received",
    "taxes",
    "total_amount",
    "split_ratio",
    "affects_cash",
)


@dataclass(frozen=True, slots=True)
class WarningOut:
    account_id: UUID
    code: str
    date: date
    transaction_id: UUID | None
    introduced: bool  # caused by this change (not present before it)


@dataclass(slots=True)
class ChangeResult:
    created: list[models.Transaction] = field(default_factory=list)
    updated: list[models.Transaction] = field(default_factory=list)
    deleted: list[UUID] = field(default_factory=list)
    warnings: list[WarningOut] = field(default_factory=list)


def apply_change(
    db: Session,
    owner_id: UUID,
    actor_id: UUID,
    *,
    creates: list[TxData] = (),
    edits_by_id: dict[UUID, TxData] | None = None,
    delete_ids: set[UUID] = frozenset(),
    today: date,
    ip: str | None = None,
) -> ChangeResult:
    """Validate and apply one atomic change. Raises ApiError (nothing written) when an id is
    unknown (404), a field rule fails (422 VALIDATION_FAILED) or a quantity would go below
    zero / a split would round a holding to zero (409 QUANTITY_CONFLICT). `today` (UTC) dates
    the FUTURE_DATE warnings."""
    edits_by_id = edits_by_id or {}
    if set(edits_by_id) & set(delete_ids):
        raise ApiError(422, "REQUEST_INVALID", "a transaction cannot be edited and deleted")

    existing = {
        r.id: r for r in txs.get_transactions(db, owner_id, set(edits_by_id) | set(delete_ids))
    }
    if len(existing) != len(set(edits_by_id) | set(delete_ids)):
        raise not_found("transaction")

    # Every account touched: the old account of edited/deleted rows and every target account.
    account_ids = {r.account_id for r in existing.values()}
    account_ids |= {d.account_id for d in creates} | {d.account_id for d in edits_by_id.values()}
    account_rows = {}
    for account_id in account_ids:
        account = accounts.get_account(db, owner_id, account_id)
        if account is None:
            raise not_found("account")
        account_rows[account_id] = account

    new_assets = {d.asset_id for d in (*creates, *edits_by_id.values()) if d.asset_id}
    for asset_id in new_assets:
        if assets.get_usable_asset(db, owner_id, asset_id) is None:
            raise not_found("asset")

    ledgers = {aid: txs.account_ledger(db, owner_id, aid) for aid in account_ids}
    current = {
        aid: [mapping.to_engine_transaction(r) for r in rows] for aid, rows in ledgers.items()
    }
    asset_ids = new_assets | {t.asset_id for ts in current.values() for t in ts if t.asset_id}
    engine_assets = {
        a.id: mapping.to_engine_asset(a) for a in assets.assets_by_id(db, asset_ids).values()
    }
    next_seq = {aid: max((t.sequence for t in ts), default=0) + 1 for aid, ts in current.items()}

    def take_sequence(account_id: UUID) -> int:
        seq = next_seq[account_id]
        next_seq[account_id] += 1
        return seq

    upserts: dict[UUID, list[engine.Transaction]] = defaultdict(list)
    deletes: dict[UUID, list[UUID]] = defaultdict(list)
    planned: dict[UUID, tuple[TxData, int]] = {}  # tx id -> (data, sequence)
    create_ids: list[UUID] = []

    for data in creates:
        tx_id = uuid.uuid4()
        seq = take_sequence(data.account_id)
        upserts[data.account_id].append(data.engine_tx(tx_id, seq))
        planned[tx_id] = (data, seq)
        create_ids.append(tx_id)
    for tx_id, data in edits_by_id.items():
        row = existing[tx_id]
        if data.account_id == row.account_id:
            seq = row.sequence  # unchanged by an edit, also when the date changes (D-70)
        else:  # moved: a new sequence in the destination account (M2 plan, answer 3)
            deletes[row.account_id].append(tx_id)
            seq = take_sequence(data.account_id)
        upserts[data.account_id].append(data.engine_tx(tx_id, seq))
        planned[tx_id] = (data, seq)
    for tx_id in delete_ids:
        deletes[existing[tx_id].account_id].append(tx_id)

    outcomes = {
        account_id: edits.apply_changes(
            current[account_id],
            engine_assets,
            upsert=upserts.get(account_id, ()),
            delete_ids=deletes.get(account_id, ()),
        )
        for account_id in sorted(account_ids)
    }
    field_issues = [
        {**_ref(tx_id, create_ids), "issues": [{"field": i.field, "code": i.code} for i in issues]}
        for outcome in outcomes.values()
        for tx_id, issues in outcome.field_issues.items()
    ]
    if field_issues:
        raise ApiError(
            422, "VALIDATION_FAILED", "a transaction breaks a field rule", transactions=field_issues
        )
    for account_id, outcome in outcomes.items():
        if (conflict := outcome.conflict) is not None:
            raise ApiError(
                409,
                "QUANTITY_CONFLICT",
                "the change would leave an invalid quantity",
                conflict={
                    "reason": conflict.code.value,
                    **_ref(conflict.transaction_id, create_ids),
                    "account_id": str(account_id),
                    "asset_id": str(conflict.asset_id),
                    "quantity": str(conflict.quantity),
                },
            )

    result = ChangeResult()
    for account_id, outcome in outcomes.items():
        old = current[account_id]
        before = _warning_keys(all_warnings(replay(old), old, today)) if old else set()
        for w in all_warnings(outcome.ledger, outcome.transactions, today):
            result.warnings.append(
                WarningOut(
                    account_id=account_id,
                    code=w.code.value,
                    date=w.date,
                    transaction_id=w.transaction_id,
                    introduced=_key(w) not in before,
                )
            )

    _persist(db, existing, planned, create_ids, delete_ids, result, actor_id, ip)
    return result


def _persist(db, existing, planned, create_ids, delete_ids, result, actor_id, ip) -> None:
    gone = [existing[tx_id] for tx_id in delete_ids]
    for row in gone:
        audit.record(
            db, "transaction.deleted", actor_id=actor_id, entity_type="transaction",
            entity_id=row.id, before=snapshot(row), ip=ip,
        )  # fmt: skip
    txs.delete_transactions(db, gone)
    result.deleted = sorted(delete_ids)

    # Edited and moved rows keep their id; a moved row also gets its new account and sequence.
    for tx_id, (data, seq) in planned.items():
        row = existing.get(tx_id)
        if row is None:
            continue
        before = snapshot(row)
        _write(row, data, data.engine_tx(tx_id, seq))
        db.flush()
        audit.record(
            db, "transaction.updated", actor_id=actor_id, entity_type="transaction",
            entity_id=tx_id, before=before, after=snapshot(row), ip=ip,
        )  # fmt: skip
        result.updated.append(row)
    for tx_id in create_ids:
        data, seq = planned[tx_id]
        row = models.Transaction()
        _write(row, data, data.engine_tx(tx_id, seq))
        db.add(row)
        db.flush()
        audit.record(
            db, "transaction.created", actor_id=actor_id, entity_type="transaction",
            entity_id=tx_id, after=snapshot(row), ip=ip,
        )  # fmt: skip
        result.created.append(row)


def _write(row: models.Transaction, data: TxData, tx: engine.Transaction) -> None:
    mapping.copy_to_row(tx, row)
    row.account_id = data.account_id
    row.external_ref = data.external_ref
    row.notes = data.notes
    row.meta = data.metadata


def snapshot(row: models.Transaction) -> dict[str, Any]:
    """Audit before/after content (decimals become exact strings in `audit.record`)."""
    data = TxData.of_row(row)
    out = {name: getattr(data, name) for name in TxData.__dataclass_fields__}
    split = out.pop("split_ratio")
    out["split_ratio"] = f"{split.new}:{split.old}" if split else None
    out["sequence"] = row.sequence
    return out


def _ref(tx_id: UUID, create_ids: list[UUID]) -> dict[str, Any]:
    """Point at the transaction in the request: existing ones by id, new ones by position."""
    if tx_id in create_ids:
        return {"index": create_ids.index(tx_id)}
    return {"transaction_id": str(tx_id)}


def all_warnings(
    ledger: Ledger,
    transactions: tuple[engine.Transaction, ...] | list[engine.Transaction],
    today: date,
) -> tuple[LedgerWarning, ...]:
    """The replay's warnings plus one per transaction dated after `today`."""
    return ledger.warnings + future_dated(transactions, today)


def _key(w: LedgerWarning) -> tuple:
    return (w.code, w.date, w.transaction_id)


def _warning_keys(warnings: tuple[LedgerWarning, ...]) -> set[tuple]:
    return {_key(w) for w in warnings}
