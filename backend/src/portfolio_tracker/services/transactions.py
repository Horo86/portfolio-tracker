"""Transaction changes validated on the final state by the engine (domain-rules §4, D-06,
D-06b, D-70, D-82 answers of the M2 plan).

Every create, edit (also moving a transaction to another account of the same owner), bulk
edit and delete goes through `apply_change`: the affected account ledgers are loaded (owner
filtered), the engine validates the fields and replays each ledger, and the change is written
only if every affected ledger is accepted. Nothing is stored when it is rejected. It must run
inside the request's write transaction (BEGIN IMMEDIATE), so validation and persistence cannot
interleave with another change.

`preview_change` runs the same validation (`plan_change`) on one change and reports it without
writing (D-139), and `preview_creates` on several creates (D-147); they only read, so they run
in a read-only transaction.
"""

import dataclasses
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker import textformats
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.db import models
from portfolio_tracker.db.types import canonical_decimal
from portfolio_tracker.engine import edits
from portfolio_tracker.engine import model as engine
from portfolio_tracker.engine.drafts import complete_amounts
from portfolio_tracker.engine.ledger import Ledger, LedgerWarning, Position, future_dated, replay
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


@dataclass(slots=True)
class Plan:
    """A validated change, not yet written: what `apply_change` persists and what the preview
    reports."""

    existing: dict[UUID, models.Transaction]  # edited and deleted rows
    accounts: dict[UUID, models.Account]  # every account touched
    current: dict[UUID, list[engine.Transaction]]  # each touched account's ledger before
    outcomes: dict[UUID, edits.Outcome]  # each touched account's ledger after (accepted)
    planned: dict[UUID, tuple[TxData, int]]  # tx id -> (data, sequence) of written rows
    create_ids: list[UUID]


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
    new_ids: Sequence[UUID] | None = None,
    import_batches: Mapping[UUID, UUID] | None = None,
    audit_each: bool = True,
) -> ChangeResult:
    """Validate and apply one atomic change. Raises ApiError (nothing written) when an id is
    unknown (404), a field rule fails (422 VALIDATION_FAILED) or a quantity would go below
    zero / a split would round a holding to zero (409 QUANTITY_CONFLICT). `today` (UTC) dates
    the FUTURE_DATE warnings.

    For an import (M3): `new_ids` gives the ids of `creates` (D-38, the app CSV keeps its
    ids), `import_batches` links each created transaction to its account's batch, and
    `audit_each=False` leaves the audit to the caller's one entry per batch (D-109)."""
    plan = plan_change(
        db, owner_id, creates=creates, edits_by_id=edits_by_id, delete_ids=delete_ids,
        new_ids=new_ids,
    )  # fmt: skip
    result = ChangeResult()
    for account_id, outcome in plan.outcomes.items():
        result.warnings += change_warnings(account_id, plan.current[account_id], outcome, today)

    _persist(db, plan.existing, plan.planned, plan.create_ids, delete_ids, result, actor_id, ip,
             import_batches or {}, audit_each)  # fmt: skip
    return result


def plan_change(
    db: Session,
    owner_id: UUID,
    *,
    creates: list[TxData] = (),
    edits_by_id: dict[UUID, TxData] | None = None,
    delete_ids: set[UUID] = frozenset(),
    new_ids: Sequence[UUID] | None = None,
) -> Plan:
    """Validate one atomic change without writing anything; raises the errors of
    `apply_change`. Reads only: also usable in a read-only transaction (the preview)."""
    edits_by_id = edits_by_id or {}
    if new_ids is not None and len(new_ids) != len(creates):
        raise ValueError("new_ids must give one id per created transaction")
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
    if assets.usable_asset_ids(db, owner_id, new_assets) != new_assets:
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

    for index, data in enumerate(creates):
        tx_id = new_ids[index] if new_ids is not None else uuid.uuid4()
        seq = take_sequence(data.account_id)
        upserts[data.account_id].append(data.engine_tx(tx_id, seq))
        planned[tx_id] = (data, seq)
        create_ids.append(tx_id)
    for tx_id, data in edits_by_id.items():
        row = existing[tx_id]
        if data.account_id == row.account_id:
            seq = row.sequence  # unchanged by an edit, also when the date changes (D-70)
        else:  # moved: a new sequence in the destination account (D-85)
            source, target = account_rows[row.account_id], account_rows[data.account_id]
            if source.currency != target.currency:  # amounts are in the account currency (D-98)
                raise ApiError(
                    409,
                    "CURRENCY_MISMATCH",
                    "a transaction can move only to an account with the same currency",
                    transaction_id=str(tx_id),
                    from_currency=source.currency,
                    to_currency=target.currency,
                )
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
                    "quantity": canonical_decimal(conflict.quantity),
                    "held": canonical_decimal(conflict.held),
                },
            )
    return Plan(existing, account_rows, current, outcomes, planned, create_ids)


@dataclass(frozen=True, slots=True)
class Preview:
    """A change as it would be (D-139): nothing is written. The figures are those of the
    account the transaction ends up in (for a delete, the one it was in), at the end of the
    whole history, before and after the change."""

    transaction: TxData | None  # the draft with its amounts completed; None for a delete
    derived: frozenset[str]  # the amounts the server filled in
    account: models.Account
    cash_before: Decimal
    cash_after: Decimal
    asset_id: UUID | None
    asset_type: engine.AssetType | None
    position_before: Position | None  # None: no asset (cash movements)
    position_after: Position | None
    warnings: list[WarningOut]


def preview_change(
    db: Session, owner_id: UUID, *, draft: TxData | None, replaces: UUID | None, today: date
) -> Preview:
    """Dry run of a create (`draft`), an edit (`replaces` + `draft`) or a delete (`replaces`
    alone), with the validation and errors of `apply_change`. Reads only."""
    if draft is None and replaces is None:
        raise ApiError(422, "REQUEST_INVALID", "give a transaction, the one it replaces, or both")
    completed, derived = None, frozenset()
    if draft is not None:
        completed, derived, too_long = _complete(db, owner_id, draft)
        if too_long:
            raise _too_long([{"index": 0, "issues": too_long}])
    if replaces is None:
        plan = plan_change(db, owner_id, creates=[completed])
    elif completed is None:
        plan = plan_change(db, owner_id, delete_ids={replaces})
    else:
        plan = plan_change(db, owner_id, edits_by_id={replaces: completed})

    target = completed or TxData.of_row(plan.existing[replaces])
    befores = {aid: replay(old) for aid, old in plan.current.items()}
    before, after = befores[target.account_id], plan.outcomes[target.account_id].ledger
    asset_id = target.asset_id

    def position(ledger: Ledger) -> Position | None:
        if asset_id is None:
            return None
        return ledger.positions.get(asset_id) or Position(asset_id=asset_id)

    # The transaction being previewed has no id yet: its warnings point at nothing.
    warnings = [
        dataclasses.replace(w, transaction_id=None) if w.transaction_id in plan.create_ids else w
        for aid, outcome in plan.outcomes.items()
        for w in change_warnings(aid, plan.current[aid], outcome, today, befores[aid])
    ]
    asset_type = None
    if asset_id is not None:
        asset_type = engine.AssetType(assets.assets_by_id(db, {asset_id})[asset_id].asset_type)
    return Preview(
        transaction=completed,
        derived=derived,
        account=plan.accounts[target.account_id],
        cash_before=before.cash,
        cash_after=after.cash,
        asset_id=asset_id,
        asset_type=asset_type,
        position_before=position(before),
        position_after=position(after),
        warnings=warnings,
    )


@dataclass(frozen=True, slots=True)
class BatchPreview:
    """Several creates as they would be (D-147): nothing is written."""

    transactions: list[TxData]  # each draft with its amounts completed, in request order
    derived: list[frozenset[str]]  # per row, the amounts the server filled in
    warnings: list[tuple[WarningOut, int | None]]  # with the row of a new transaction


def preview_creates(
    db: Session, owner_id: UUID, drafts: list[TxData], *, today: date
) -> BatchPreview:
    """Dry run of creating `drafts` together, with the validation and errors of
    `apply_change`; errors about a draft name its position (`index`). A derived amount too
    long to save is reported for every row first, then the field rules of every row. Reads
    only."""
    completed, derived, too_long = [], [], []
    for index, draft in enumerate(drafts):
        done, names, issues = _complete(db, owner_id, draft)
        completed.append(done)
        derived.append(names)
        if issues:
            too_long.append({"index": index, "issues": issues})
    if too_long:
        raise _too_long(too_long)
    plan = plan_change(db, owner_id, creates=completed)

    warnings = []
    for aid, outcome in plan.outcomes.items():
        for w in change_warnings(aid, plan.current[aid], outcome, today):
            if w.transaction_id in plan.create_ids:  # a new row has no id yet
                index = plan.create_ids.index(w.transaction_id)
                warnings.append((dataclasses.replace(w, transaction_id=None), index))
            else:
                warnings.append((w, None))
    return BatchPreview(completed, derived, warnings)


def _complete(
    db: Session, owner_id: UUID, draft: TxData
) -> tuple[TxData, frozenset[str], list[dict[str, str]]]:
    """The draft with `gross_amount` and `total_amount` filled in where the user left them
    out (engine.drafts), the names of the amounts filled in, and the field issues of a
    derived amount too long to save; 404 for an account or asset that is not the user's."""
    account = accounts.get_account(db, owner_id, draft.account_id)
    if account is None:
        raise not_found("account")
    asset = None
    if draft.asset_id is not None:
        if not assets.usable_asset_ids(db, owner_id, {draft.asset_id}):
            raise not_found("asset")
        asset = mapping.to_engine_asset(assets.assets_by_id(db, {draft.asset_id})[draft.asset_id])
    done = complete_amounts(draft.engine_tx(uuid.uuid4(), 0), asset, account.currency)
    completed = dataclasses.replace(
        draft,
        gross_amount=done.transaction.gross_amount,
        total_amount=done.transaction.total_amount,
    )
    # A derived amount must fit the write routes (D-98), or Save would fail on a figure the
    # user never typed: they type the broker's amount instead.
    too_long = [
        {"field": name, "code": "too_many_digits"}
        for name in sorted(done.derived)
        if not textformats.DECIMAL.fullmatch(canonical_decimal(abs(getattr(completed, name))))
    ]
    return completed, done.derived, too_long


def _too_long(rows: list[dict[str, Any]]) -> ApiError:
    return ApiError(
        422, "VALIDATION_FAILED", "a transaction breaks a field rule", transactions=rows
    )


def change_warnings(
    account_id: UUID,
    old: list[engine.Transaction],
    outcome: edits.Outcome,
    today: date,
    before: Ledger | None = None,
) -> list[WarningOut]:
    """The warnings of an accepted change, each saying whether the change introduced it.
    `before` is the replay of `old`, when the caller already has it."""
    known = _warning_keys(all_warnings(before or replay(old), old, today)) if old else set()
    return [
        WarningOut(
            account_id=account_id,
            code=w.code.value,
            date=w.date,
            transaction_id=w.transaction_id,
            introduced=_key(w) not in known,
        )
        for w in all_warnings(outcome.ledger, outcome.transactions, today)
    ]


def _persist(db, existing, planned, create_ids, delete_ids, result, actor_id, ip,
             import_batches, audit_each) -> None:  # fmt: skip
    def record(action: str, **fields: Any) -> None:
        if audit_each:
            audit.record(db, action, actor_id=actor_id, entity_type="transaction", ip=ip,
                         **fields)  # fmt: skip

    gone = [existing[tx_id] for tx_id in delete_ids]
    for row in gone:
        record("transaction.deleted", entity_id=row.id, before=snapshot(row))
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
        record("transaction.updated", entity_id=tx_id, before=before, after=snapshot(row))
        result.updated.append(row)
    for tx_id in create_ids:  # one flush for all of them (an import creates thousands)
        data, seq = planned[tx_id]
        row = models.Transaction(import_batch_id=import_batches.get(data.account_id))
        _write(row, data, data.engine_tx(tx_id, seq))
        result.created.append(row)
    db.add_all(result.created)
    db.flush()
    for row in result.created:
        record("transaction.created", entity_id=row.id, after=snapshot(row))


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
