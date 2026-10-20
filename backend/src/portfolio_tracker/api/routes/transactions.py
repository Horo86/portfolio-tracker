"""Transactions: list, create, edit (also to another account), bulk edit, delete, and
"delete all transactions of an asset in an account" (domain-rules §2, §4; D-06, D-06b).

Every change is validated on the final state by the engine and recalculated before the
response returns. Rejections: 422 VALIDATION_FAILED (field rules, per transaction), 409
QUANTITY_CONFLICT (first transaction that would leave an invalid quantity). Warnings
(negative cash, income on a never-held asset, a date in the future) never reject; each says
whether this change introduced it.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from portfolio_tracker import textformats
from portfolio_tracker.api.deps import CurrentAuth, DbSession, Now, ShortAuth, transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.accounts import owned_account
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.routes.reports import shown_pmc
from portfolio_tracker.api.schemas import DecimalStr, Strict
from portfolio_tracker.db import models
from portfolio_tracker.db.types import canonical_decimal
from portfolio_tracker.engine.model import SplitRatio, TransactionType
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.repositories.transactions import TransactionFilter
from portfolio_tracker.services.transactions import (
    ChangeResult,
    TxData,
    WarningOut,
    apply_change,
    preview_change,
)

router = APIRouter(tags=["transactions"])

MAX_BATCH = 1000

_SPLIT = textformats.SPLIT_RATIO


def _split_ratio(value: str) -> str:
    if not _SPLIT.fullmatch(value):
        raise ValueError('must be "new:old", e.g. "2:1" (split) or "1:10" (reverse split)')
    return value


SplitRatioStr = Annotated[str, AfterValidator(_split_ratio)]
Notes = Annotated[str, StringConstraints(max_length=2000)]


def _metadata(value: dict[str, Any]) -> dict[str, Any]:
    if not textformats.metadata_fits(value):
        raise ValueError(
            f"must be JSON (no NaN or Infinity) of at most {textformats.MAX_METADATA_CHARS} "
            "characters"
        )
    return value


# D-114: the same measure as the app CSV, so every transaction survives export and import.
Metadata = Annotated[dict[str, Any], AfterValidator(_metadata)]
ExternalRef = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]


class TransactionFields(Strict):
    """Writable fields (domain-rules §2). Amounts are decimal strings in account currency;
    `unit_price` is in the asset quote unit. A field a type does not have must be omitted
    (D-62, D-67)."""

    account_id: UUID
    type: TransactionType
    trade_date: date
    trade_time: time | None = None
    value_date: date | None = None
    asset_id: UUID | None = None
    quantity: DecimalStr | None = None
    unit_price: DecimalStr | None = None
    gross_amount: DecimalStr | None = None
    fees: DecimalStr | None = None
    accrued_interest_paid: DecimalStr | None = None
    accrued_interest_received: DecimalStr | None = None
    taxes: DecimalStr | None = None
    total_amount: DecimalStr | None = None
    split_ratio: SplitRatioStr | None = None
    affects_cash: bool = True
    external_ref: ExternalRef | None = None
    notes: Notes | None = None
    metadata: Metadata | None = None

    def data(self) -> TxData:
        fields = self.model_dump()
        split = fields.pop("split_ratio")
        if split is not None:
            new, old = _SPLIT.fullmatch(split).groups()
            fields["split_ratio"] = SplitRatio(new=_dec(new), old=_dec(old))
        fields["external_ref"] = fields["external_ref"] or None
        return TxData(**fields)


class TransactionPatch(Strict):
    """Bulk edit: only the fields sent change (null clears an optional field)."""

    id: UUID
    account_id: UUID | None = None
    type: TransactionType | None = None
    trade_date: date | None = None
    trade_time: time | None = None
    value_date: date | None = None
    asset_id: UUID | None = None
    quantity: DecimalStr | None = None
    unit_price: DecimalStr | None = None
    gross_amount: DecimalStr | None = None
    fees: DecimalStr | None = None
    accrued_interest_paid: DecimalStr | None = None
    accrued_interest_received: DecimalStr | None = None
    taxes: DecimalStr | None = None
    total_amount: DecimalStr | None = None
    split_ratio: SplitRatioStr | None = None
    affects_cash: bool | None = None
    external_ref: ExternalRef | None = None
    notes: Notes | None = None
    metadata: Metadata | None = None


class BulkEditIn(Strict):
    edits: list[TransactionPatch] = Field(min_length=1, max_length=MAX_BATCH)


class BulkDeleteIn(Strict):
    ids: list[UUID] = Field(min_length=1, max_length=MAX_BATCH)


class TransactionOut(BaseModel):
    id: UUID
    account_id: UUID
    sequence: int
    type: TransactionType
    trade_date: date
    trade_time: time | None
    value_date: date | None
    asset_id: UUID | None
    quantity: DecimalStr | None
    unit_price: DecimalStr | None
    gross_amount: DecimalStr | None
    fees: DecimalStr | None
    accrued_interest_paid: DecimalStr | None
    accrued_interest_received: DecimalStr | None
    taxes: DecimalStr | None
    total_amount: DecimalStr | None
    split_ratio: str | None
    affects_cash: bool
    external_ref: str | None
    notes: str | None
    metadata: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: models.Transaction) -> "TransactionOut":
        split = None
        if row.split_new is not None:
            split = f"{canonical_decimal(row.split_new)}:{canonical_decimal(row.split_old)}"
        return cls(
            **{
                name: getattr(row, name)
                for name in cls.model_fields
                if name not in ("split_ratio", "metadata")
            },
            split_ratio=split,
            metadata=row.meta,
        )


class WarningModel(BaseModel):
    account_id: UUID
    code: str  # NEGATIVE_CASH | INCOME_WITHOUT_HOLDING | FUTURE_DATE
    date: date
    transaction_id: UUID | None
    introduced: bool

    @classmethod
    def of(cls, w: WarningOut) -> "WarningModel":
        return cls(
            account_id=w.account_id,
            code=w.code,
            date=w.date,
            transaction_id=w.transaction_id,
            introduced=w.introduced,
        )


class ChangeOut(BaseModel):
    """Result of a change: the written transactions, the deleted ids, and every warning of the
    affected accounts after the change."""

    transactions: list[TransactionOut]
    deleted: list[UUID]
    warnings: list[WarningModel]


class PreviewIn(Strict):
    """A create (`transaction`), an edit (`replaces` + `transaction`) or a delete (`replaces`
    alone). `gross_amount` and `total_amount` may be left out of the transaction: the server
    fills them in (D-139)."""

    replaces: UUID | None = None
    transaction: TransactionFields | None = None


class CashPreview(BaseModel):
    before: DecimalStr
    change: DecimalStr
    after: DecimalStr


class PositionPreview(BaseModel):
    asset_id: UUID
    quantity_before: DecimalStr
    quantity_after: DecimalStr
    cost_basis_before: DecimalStr
    cost_basis_after: DecimalStr
    pmc_before: DecimalStr | None  # null when nothing is held; bonds as % of nominal (D-66)
    pmc_after: DecimalStr | None


class PreviewOut(BaseModel):
    """The change as it would be; nothing is stored. Figures are in the account currency, for
    the account the transaction ends up in (for a delete, the one it was in), at the end of
    the whole history. Warnings cover every account the change touches."""

    transaction: TransactionFields | None  # the body to save with POST or PUT; null: delete
    derived: list[str]  # amounts the server filled in: gross_amount, total_amount
    account_id: UUID
    currency: str
    cash: CashPreview
    position: PositionPreview | None  # null for DEPOSIT and WITHDRAWAL
    warnings: list[WarningModel]


class TransactionPage(BaseModel):
    items: list[TransactionOut]
    total: int


def _dec(text: str) -> Decimal:
    return Decimal(text)


def _out(result: ChangeResult) -> ChangeOut:
    return ChangeOut(
        transactions=[TransactionOut.of(r) for r in (*result.created, *result.updated)],
        deleted=result.deleted,
        warnings=[WarningModel.of(w) for w in result.warnings],
    )


_CONFLICT_DOC = {
    404: {"description": "NOT_FOUND (transaction, account or asset of another user)"},
    409: {"description": "QUANTITY_CONFLICT"},
    422: {"description": "VALIDATION_FAILED or REQUEST_INVALID"},
}


@router.get("/transactions")
def list_transactions(
    auth: CurrentAuth,
    db: DbSession,
    account_id: UUID | None = None,
    asset_id: UUID | None = None,
    type: Annotated[list[TransactionType] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionPage:
    """The user's transactions, newest first."""
    flt = TransactionFilter(
        account_id=account_id,
        asset_id=asset_id,
        types=tuple(t.value for t in type or ()),
        date_from=date_from,
        date_to=date_to,
    )
    rows, total = txs.list_transactions(db, auth.user.id, flt, limit=limit, offset=offset)
    return TransactionPage(items=[TransactionOut.of(r) for r in rows], total=total)


@router.post("/transactions/preview", responses=_CONFLICT_DOC)
def preview_transaction(body: PreviewIn, auth: ShortAuth, request: Request, at: Now) -> PreviewOut:
    """Dry run of one change (D-139): the same validation and errors as the write routes, in a
    read-only transaction (never the write lock), and nothing is stored. The returned
    `transaction` is what the form saves."""
    with transaction(request.app, write=False) as db:
        result = preview_change(
            db,
            auth.user.id,
            draft=body.transaction.data() if body.transaction else None,
            replaces=body.replaces,
            today=at.date(),
        )
        completed = None
        if body.transaction is not None:
            done = result.transaction
            completed = body.transaction.model_copy(
                update={"gross_amount": done.gross_amount, "total_amount": done.total_amount}
            )
        position = None
        if result.asset_id is not None:
            before, after = result.position_before, result.position_after
            position = PositionPreview(
                asset_id=result.asset_id,
                quantity_before=before.quantity,
                quantity_after=after.quantity,
                cost_basis_before=before.cost_basis,
                cost_basis_after=after.cost_basis,
                pmc_before=shown_pmc(before, result.asset_type),
                pmc_after=shown_pmc(after, result.asset_type),
            )
        return PreviewOut(
            transaction=completed,
            derived=sorted(result.derived),
            account_id=result.account.id,
            currency=result.account.currency,
            cash=CashPreview(
                before=result.cash_before,
                change=result.cash_after - result.cash_before,
                after=result.cash_after,
            ),
            position=position,
            warnings=[WarningModel.of(w) for w in result.warnings],
        )


@router.get("/transactions/{tx_id}")
def get_transaction(tx_id: UUID, auth: CurrentAuth, db: DbSession) -> TransactionOut:
    row = txs.get_transaction(db, auth.user.id, tx_id)
    if row is None:
        raise not_found("transaction")
    return TransactionOut.of(row)


@router.post("/transactions", status_code=status.HTTP_201_CREATED, responses=_CONFLICT_DOC)
def create_transaction(
    body: TransactionFields, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> ChangeOut:
    result = apply_change(
        db,
        auth.user.id,
        auth.user.id,
        creates=[body.data()],
        ip=client_ip(request),
        today=at.date(),
    )
    return _out(result)


@router.put("/transactions/{tx_id}", responses=_CONFLICT_DOC)
def edit_transaction(
    tx_id: UUID,
    body: TransactionFields,
    auth: CurrentAuth,
    request: Request,
    db: DbSession,
    at: Now,
) -> ChangeOut:
    """Atomic edit, validated on the final state only (§4). Changing `account_id` moves the
    transaction to another account of the user; both ledgers must stay valid."""
    result = apply_change(
        db,
        auth.user.id,
        auth.user.id,
        edits_by_id={tx_id: body.data()},
        ip=client_ip(request),
        today=at.date(),
    )
    return _out(result)


@router.post("/transactions/bulk-edit", responses=_CONFLICT_DOC)
def bulk_edit(
    body: BulkEditIn, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> ChangeOut:
    """Several edits validated together as one change (D-06b): a state that no single edit
    can fix is fixed by editing the transactions at once."""
    ids = [patch.id for patch in body.edits]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "REQUEST_INVALID", "a transaction appears twice")
    rows = {r.id: r for r in txs.get_transactions(db, auth.user.id, set(ids))}
    if len(rows) != len(ids):
        raise not_found("transaction")
    edits = {}
    for patch in body.edits:
        current = TransactionFields.model_validate(_fields_of(rows[patch.id]))
        changes = patch.model_dump(exclude_unset=True, exclude={"id"})
        for name in ("account_id", "type", "trade_date", "affects_cash"):
            if name in changes and changes[name] is None:
                raise ApiError(422, "REQUEST_INVALID", f"{name} cannot be null")
        edits[patch.id] = current.model_copy(update=changes).data()
    result = apply_change(
        db, auth.user.id, auth.user.id, edits_by_id=edits, ip=client_ip(request), today=at.date()
    )
    return _out(result)


@router.delete("/transactions/{tx_id}", responses=_CONFLICT_DOC)
def delete_transaction(
    tx_id: UUID, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> ChangeOut:
    """A real delete with an audit entry (D-07); rejected if a later transaction needs it."""
    result = apply_change(
        db, auth.user.id, auth.user.id, delete_ids={tx_id}, ip=client_ip(request), today=at.date()
    )
    return _out(result)


@router.post("/transactions/bulk-delete", responses=_CONFLICT_DOC)
def bulk_delete(
    body: BulkDeleteIn, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> ChangeOut:
    result = apply_change(
        db,
        auth.user.id,
        auth.user.id,
        delete_ids=set(body.ids),
        ip=client_ip(request),
        today=at.date(),
    )
    return _out(result)


@router.delete("/accounts/{account_id}/assets/{asset_id}/transactions", responses=_CONFLICT_DOC)
def delete_all_for_asset(
    account_id: UUID, asset_id: UUID, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> ChangeOut:
    """Delete every transaction of an asset in one account (D-06b): always possible, since
    no other asset's quantity depends on them."""
    owned_account(db, auth, account_id)
    ids = {r.id for r in txs.account_ledger(db, auth.user.id, account_id) if r.asset_id == asset_id}
    if not ids:
        return ChangeOut(transactions=[], deleted=[], warnings=[])
    result = apply_change(
        db, auth.user.id, auth.user.id, delete_ids=ids, ip=client_ip(request), today=at.date()
    )
    return _out(result)


def _fields_of(row: models.Transaction) -> dict[str, Any]:
    out = TransactionOut.of(row).model_dump(mode="json")
    for name in ("id", "sequence", "created_at", "updated_at"):
        out.pop(name)
    return out
