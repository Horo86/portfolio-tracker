"""Imports: preview, confirm, and the confirmed batches (docs/import/; D-38, D-99 to D-113;
mockups 07-09).

Stateless (D-100): the preview stores nothing; the confirmation sends the same file and
inputs again, with the selected line keys and the preview's fingerprint. The file travels as
base64 in the JSON body (D-101); the body is bounded before it is read (`api.limits`).

Both decode and parse the file without holding a database connection (the session is
resolved in its own short transaction), inside one of a few process-wide import slots
(`import_slot`, D-114). Then the preview reads in a read-only transaction; the confirmation
takes the write lock, inside which the service previews again and writes.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field, StringConstraints

from portfolio_tracker.api.deps import CurrentAuth, DbSession, Now, ShortAuth
from portfolio_tracker.api.deps import transaction as db_transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.accounts import owned_account
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.routes.transactions import TransactionOut
from portfolio_tracker.api.schemas import Currency, DecimalStr, Strict
from portfolio_tracker.db import models
from portfolio_tracker.db.types import canonical_decimal
from portfolio_tracker.engine.model import AssetType, QuoteUnit, TransactionType
from portfolio_tracker.importers.app_csv import MAX_ACCOUNTS, MAX_ROWS
from portfolio_tracker.importers.base import Category, Reason
from portfolio_tracker.repositories import imports as batches
from portfolio_tracker.services import exports, imports
from portfolio_tracker.services.imports import AssetChoice, ImportInput, ImportWarning, Line

router = APIRouter(prefix="/imports", tags=["imports"])
# Under /transactions, so registered before `/transactions/{tx_id}` (see `api.routes`).
export_router = APIRouter(tags=["imports"])

FileName = Annotated[str, StringConstraints(max_length=255)]
LineKey = Annotated[str, StringConstraints(max_length=100)]
AccountName = Annotated[str, StringConstraints(max_length=100)]
AssetKey = Annotated[str, StringConstraints(max_length=300)]
Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class AssetChoiceIn(Strict):
    """An existing asset (only for a CSV row without ISIN), or what the new asset is."""

    asset_id: UUID | None = None
    asset_type: AssetType | None = None
    quote_currency: Currency | None = None
    quote_unit: QuoteUnit | None = None


class ImportIn(Strict):
    """The file and the user's choices. Send the same values to the confirmation."""

    content_base64: str  # the file, at most 10 MB once decoded (D-101)
    file_name: FileName | None = None
    format: Annotated[str, StringConstraints(max_length=32)] | None = None  # None: detected
    move_cash: bool = True  # false: "do not move cash" (D-49, D-112)
    account_id: UUID | None = None  # Directa: the target account (default: by account code)
    # App CSV: file account name -> an existing account id, or null to create it.
    accounts: dict[AccountName, UUID | None] = Field(default_factory=dict, max_length=MAX_ACCOUNTS)
    assets: dict[AssetKey, AssetChoiceIn] = Field(default_factory=dict, max_length=MAX_ROWS)
    # Line keys to import; omitted: the default selection.
    selected: list[LineKey] | None = Field(default=None, max_length=MAX_ROWS)

    def input(self, content: bytes) -> ImportInput:
        return ImportInput(
            content=content,
            file_name=self.file_name,
            source=self.format,
            move_cash=self.move_cash,
            account_id=self.account_id,
            accounts=dict(self.accounts),
            assets={k: AssetChoice(**v.model_dump()) for k, v in self.assets.items()},
            selected=frozenset(self.selected) if self.selected is not None else None,
        )


class ConfirmIn(ImportIn):
    fingerprint: Fingerprint  # from the preview of the same file and inputs
    selected: list[LineKey] = Field(max_length=MAX_ROWS)  # what the user ticked


class ReasonOut(BaseModel):
    code: str  # stable; the UI translates it with its params
    params: dict[str, str]

    @classmethod
    def of(cls, r: Reason) -> "ReasonOut":
        return cls(code=r.code, params=r.as_dict())


class LineOut(BaseModel):
    """One preview line: a transaction to import, or the rows that are not imported."""

    key: str
    category: Category
    selectable: bool
    selected: bool
    lines: list[int]  # spreadsheet rows of the file
    reasons: list[ReasonOut]
    info: list[ReasonOut]
    account: str | None  # key of `accounts`
    asset: str | None  # key of `assets`
    type: TransactionType | None
    trade_date: date | None
    trade_time: time | None
    value_date: date | None
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
    account_expense: bool  # stamp duty / Tobin tax, grouped in the preview
    original_isin: str | None  # as written in the file
    original_name: str | None

    @classmethod
    def of(cls, line: Line) -> "LineOut":
        c = line.candidate
        split = c.split_ratio
        return cls(
            key=c.key,
            category=c.category,
            selectable=c.selectable,
            selected=line.selected,
            lines=list(c.lines),
            reasons=[ReasonOut.of(r) for r in c.reasons],
            info=[ReasonOut.of(r) for r in c.info],
            account=line.account_key,
            asset=line.asset_key,
            split_ratio=(
                f"{canonical_decimal(split.new)}:{canonical_decimal(split.old)}" if split else None
            ),
            metadata=dict(c.metadata) if c.metadata is not None else None,
            original_isin=c.asset.isin if c.asset else None,
            original_name=c.asset.name if c.asset else None,
            **{
                name: getattr(c, name)
                for name in (
                    "type",
                    "trade_date",
                    "trade_time",
                    "value_date",
                    "quantity",
                    "unit_price",
                    "gross_amount",
                    "fees",
                    "accrued_interest_paid",
                    "accrued_interest_received",
                    "taxes",
                    "total_amount",
                    "affects_cash",
                    "external_ref",
                    "notes",
                    "account_expense",
                )
            },
        )


class AccountPlanOut(BaseModel):
    key: str | None  # the CSV account name; null for a Directa file
    account_id: UUID | None  # the existing target; null: created at confirmation
    new: bool
    name: str
    currency: str | None
    provider: str | None


class AssetPlanOut(BaseModel):
    key: str
    asset_id: UUID | None  # an existing asset; null: a new private asset
    new: bool
    name: str
    isin: str | None
    ticker: str | None
    asset_type: AssetType | None  # null: the user must choose (D-102)
    quote_currency: str | None
    quote_unit: QuoteUnit | None
    options: list[UUID]  # the user's assets with the same ticker (CSV row without ISIN)


class ImportWarningOut(BaseModel):
    code: str  # NEGATIVE_CASH | INCOME_WITHOUT_HOLDING | FUTURE_DATE
    date: date
    account: str | None  # key of `accounts`
    line: str | None  # the preview line, or null
    transaction_id: UUID | None  # an existing transaction, or null
    introduced: bool

    @classmethod
    def of(cls, w: ImportWarning) -> "ImportWarningOut":
        return cls(code=w.code, date=w.date, account=w.account_key, line=w.line,
                   transaction_id=w.transaction_id, introduced=w.introduced)  # fmt: skip


class CountsOut(BaseModel):
    selected: int
    to_import: int
    to_review: int
    skipped_by_choice: int
    duplicates: int
    not_recognized: int
    ambiguous: int
    invalid: int


class PreviewOut(BaseModel):
    format: str
    file_name: str | None
    rows_read: int  # data rows of the file (mockup 08)
    move_cash: bool
    account_code: str | None  # Directa account code of the file
    counts: CountsOut
    accounts: list[AccountPlanOut]
    assets: list[AssetPlanOut]
    lines: list[LineOut]
    notices: list[ReasonOut]
    blocking: list[ReasonOut]  # to solve before confirming
    warnings: list[ImportWarningOut]
    fingerprint: str  # send it back with the confirmation (D-100)


class BatchOut(BaseModel):
    id: UUID
    account_id: UUID
    format: str
    file_name: str | None
    move_cash: bool
    imported: int
    duplicates: int
    not_recognized: int
    to_review_skipped: int
    ambiguous: int
    invalid: int
    skipped_by_choice: int
    transactions: int  # still linked to the batch: deleted ones are gone, moved ones count
    created_at: datetime


class ConfirmOut(BaseModel):
    batches: list[BatchOut]
    transactions: list[TransactionOut]
    accounts_created: list[UUID]
    assets_created: list[UUID]
    warnings: list[ImportWarningOut]


def _preview_out(p: imports.Preview) -> PreviewOut:
    c = Category
    return PreviewOut(
        format=p.source,
        file_name=p.file_name,
        rows_read=sum(len(line.candidate.lines) for line in p.lines),
        move_cash=p.move_cash,
        account_code=p.account_code,
        counts=CountsOut(
            selected=p.selected_count,
            to_import=p.count(c.TO_IMPORT),
            to_review=p.count(c.TO_REVIEW),
            skipped_by_choice=p.count(c.SKIPPED_BY_CHOICE),
            duplicates=p.count(c.DUPLICATE),
            not_recognized=p.count(c.NOT_RECOGNIZED),
            ambiguous=p.count(c.AMBIGUOUS),
            invalid=p.count(c.INVALID),
        ),
        accounts=[
            AccountPlanOut(
                key=a.key,
                account_id=a.account_id,
                new=a.new,
                name=a.name,
                currency=a.currency,
                provider=a.provider,
            )
            for a in p.accounts
        ],
        assets=[
            AssetPlanOut(
                key=a.key,
                asset_id=a.asset_id,
                new=a.new,
                name=a.name,
                isin=a.isin,
                ticker=a.ticker,
                asset_type=a.asset_type,
                quote_currency=a.quote_currency,
                quote_unit=a.quote_unit,
                options=list(a.options),
            )
            for a in p.assets
        ],
        lines=[LineOut.of(line) for line in p.lines],
        notices=[ReasonOut.of(r) for r in p.notices],
        blocking=[ReasonOut.of(r) for r in p.blocking],
        warnings=[ImportWarningOut.of(w) for w in p.warnings],
        fingerprint=p.fingerprint,
    )


def batch_out(db, batch: models.ImportBatch) -> BatchOut:
    return BatchOut(
        format=batch.source,
        transactions=batches.count_batch_transactions(db, batch),
        **{
            name: getattr(batch, name)
            for name in BatchOut.model_fields
            if name not in ("format", "transactions")
        },
    )


_DOC = {
    404: {"description": "NOT_FOUND (an account or asset of another user)"},
    413: {"description": "IMPORT_TOO_LARGE"},
    422: {
        "description": "IMPORT_FILE_INVALID, IMPORT_FORMAT_UNKNOWN, IMPORT_FORMAT_VERSION, "
        "IMPORT_ACCOUNT_REQUIRED, IMPORT_ACCOUNT_CURRENCY, IMPORT_CHOICE_INVALID, "
        "IMPORT_SELECTION_INVALID or REQUEST_INVALID"
    },
}


# At most this many imports are decoded, parsed and previewed at once in the process (CSV
# included; an xlsx is also parsed one at a time, D-101). Another one waits, then gets 503
# IMPORT_BUSY: a few large uploads cannot exhaust memory, threads or database connections.
MAX_CONCURRENT_IMPORTS = 2
IMPORT_WAIT_SECONDS = 10.0
RETRY_AFTER_SECONDS = 5
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_IMPORTS)


@contextmanager
def import_slot() -> Iterator[None]:
    if not _SLOTS.acquire(timeout=IMPORT_WAIT_SECONDS):
        raise ApiError(503, "IMPORT_BUSY", "other imports are running: try again shortly",
                       retry_after=RETRY_AFTER_SECONDS)  # fmt: skip
    try:
        yield
    finally:
        _SLOTS.release()


_BUSY = {503: {"description": "IMPORT_BUSY (with Retry-After)"}}


@router.post("/preview", responses=_DOC | _BUSY)
def preview_import(body: ImportIn, auth: ShortAuth, request: Request, at: Now) -> PreviewOut:
    """What the import would do; nothing is stored (D-100). Call it again after changing the
    account, the option, an asset choice or the selection.

    No database connection is held while the file is decoded and parsed: the session is
    resolved in its own short transaction, the preview reads in another one afterwards."""
    with import_slot():
        inp = body.input(imports.decode(body.content_base64))
        parsed = imports.parse(inp)
        with db_transaction(request.app, write=False) as db:
            result = imports.preview(db, auth.user.id, inp, today=at.date(), parsed=parsed)
        return _preview_out(result)


@router.post(
    "/confirm",
    status_code=status.HTTP_201_CREATED,
    responses=_DOC | _BUSY | {
        409: {"description": "IMPORT_PREVIEW_STALE or QUANTITY_CONFLICT"},
        422: {"description": _DOC[422]["description"] + ", IMPORT_NOT_READY"},
    },
)  # fmt: skip
def confirm_import(body: ConfirmIn, auth: ShortAuth, request: Request, at: Now) -> ConfirmOut:
    """Import the selected lines exactly as previewed, or nothing (hard rule 6). The file is
    parsed before the write transaction opens; inside it the service previews again."""
    with import_slot():
        inp = body.input(imports.decode(body.content_base64))
        parsed = imports.parse(inp)
        with db_transaction(request.app, write=True) as db:
            if db.get(models.UserSession, auth.session.id) is None:  # logged out meanwhile
                raise ApiError(401, "NOT_AUTHENTICATED", "a valid session is required")
            done = imports.confirm(
                db, auth.user.id, auth.user.id, inp, body.fingerprint,
                today=at.date(), ip=client_ip(request), parsed=parsed,
            )  # fmt: skip
            return ConfirmOut(
                batches=[batch_out(db, b) for b in done.batches],
                transactions=[TransactionOut.of(r) for r in done.created],
                accounts_created=list(done.accounts_created),
                assets_created=list(done.assets_created),
                warnings=[ImportWarningOut.of(w) for w in done.warnings],
            )


@router.get("")
def list_imports(
    auth: CurrentAuth, db: DbSession, account_id: UUID | None = None
) -> list[BatchOut]:
    """The user's confirmed imports, newest first (mockup 09); one account's with `account_id`."""
    if account_id is not None:
        owned_account(db, auth, account_id)
    return [batch_out(db, b) for b in batches.list_batches(db, auth.user.id, account_id)]


@router.get("/{batch_id}")
def get_import(batch_id: UUID, auth: CurrentAuth, db: DbSession) -> BatchOut:
    batch = batches.get_batch(db, auth.user.id, batch_id)
    if batch is None:
        raise not_found("import")
    return batch_out(db, batch)


@export_router.get(
    "/transactions/export.csv",
    response_class=Response,
    responses={
        200: {"content": {"text/csv": {}}, "description": "The app CSV, version 1"},
        404: {"description": "NOT_FOUND (an account of another user)"},
    },
)
def export_transactions(auth: CurrentAuth, db: DbSession, account_id: UUID | None = None):
    """Every transaction of the user, or of one account, as the app CSV (D-107): importing it
    back reproduces the ledger without duplicates (D-38)."""
    text = exports.export_csv(db, auth.user.id, account_id)
    return Response(
        content=text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transactions.csv"'},
    )
