"""Import preview and confirmation (`docs/import/`; D-38, D-49, D-99 to D-112).

Stateless (D-100): `preview` reads the file and compares it with the user's data without
writing anything. It resolves the target accounts and the assets (by ISIN among the assets
the user can see, otherwise a proposed new private asset, D-102), finds what is already
present, checks every selectable transaction with the engine's field rules and replays each
affected ledger with the selection, as a dry run. The result carries a fingerprint of
everything the user is shown except what depends on the selection.

`confirm` runs the same preview inside the request's write transaction, refuses it when the
fingerprint differs from the one the user saw, then creates the accounts and assets the
selected lines need, one import batch per account, the transactions through `apply_change`
(the engine validates every affected ledger again) and one audit entry per batch (D-109).

Every list is in file order, and keys are stable for the same file and inputs: confirmation
refers to preview lines by key.
"""

import hashlib
import hmac
import json
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker import textformats
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.db import models
from portfolio_tracker.db.types import canonical_decimal
from portfolio_tracker.engine import edits
from portfolio_tracker.engine import model as engine
from portfolio_tracker.engine.model import AssetType, QuoteUnit, TransactionType
from portfolio_tracker.engine.validation import validate_asset, validate_transaction
from portfolio_tracker.importers.base import (
    AssetRef,
    Candidate,
    Category,
    ImportFileError,
    ParsedImport,
    Reason,
    decode_upload,
    derived_unit_price,
    reason,
)
from portfolio_tracker.importers.formats import importer_for
from portfolio_tracker.repositories import accounts, assets, audit, mapping
from portfolio_tracker.repositories import imports as batches
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services.transactions import TxData, apply_change, change_warnings

log = logging.getLogger(__name__)
T = TransactionType
DIRECTA = "directa-xlsx"
APP_CSV = "app-csv"
_RELATED_TO_PURCHASE = frozenset({T.DIVIDEND, T.COUPON, T.SELL})


# --- What the user sends ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetChoice:
    """The user's choice for one asset of the file (D-102, D-111): an existing asset (only for
    a CSV row without ISIN), or what the new asset is. Omitted fields keep the proposal."""

    asset_id: UUID | None = None
    asset_type: AssetType | None = None
    quote_currency: str | None = None
    quote_unit: QuoteUnit | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportInput:
    """The preview request; confirmation sends the same values again (D-100)."""

    content: bytes
    file_name: str | None = None
    source: str | None = None  # None: detected from the content (mockup 07)
    move_cash: bool = True  # False: "do not move cash" (D-49, D-112)
    # Directa: the target account; when omitted, the account remembering the file's account
    # code (`directa.md` §4).
    account_id: UUID | None = None
    # App CSV: file account name -> an existing account, or None to create it (D-38). A name
    # left out gets the proposal: an account with the same name and currency, or a new one.
    accounts: Mapping[str, UUID | None] = field(default_factory=dict)
    assets: Mapping[str, AssetChoice] = field(default_factory=dict)  # by asset key
    selected: frozenset[str] | None = None  # line keys; None: the default selection


# --- What the preview returns -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccountPlan:
    key: str | None  # the CSV account name; None for a Directa file (one account)
    account_id: UUID | None  # the existing target; None: created at confirmation
    name: str
    currency: str | None
    provider: str | None = None

    @property
    def new(self) -> bool:
        return self.account_id is None


@dataclass(frozen=True, slots=True)
class AssetPlan:
    key: str  # "isin:<ISIN>"; for a CSV row without ISIN "ticker:<ticker>" or "name:<name>"
    asset_id: UUID | None  # an existing asset; None: a new private asset (D-37)
    name: str
    isin: str | None = None
    ticker: str | None = None
    asset_type: AssetType | None = None
    quote_currency: str | None = None
    quote_unit: QuoteUnit | None = None
    options: tuple[UUID, ...] = ()  # visible assets with the same ticker (CSV, no ISIN)

    @property
    def new(self) -> bool:
        return self.asset_id is None

    @property
    def complete(self) -> bool:
        return not self.new or (
            self.asset_type is not None
            and self.quote_currency is not None
            and self.quote_unit is not None
        )


@dataclass(frozen=True, slots=True)
class Line:
    """One preview line: the candidate with its final category, reasons and notes."""

    candidate: Candidate
    account_key: str | None
    asset_key: str | None
    selected: bool

    @property
    def key(self) -> str:
        return self.candidate.key


@dataclass(frozen=True, slots=True)
class ImportWarning:
    """A ledger warning of the would-be state; `line` is the preview line it concerns, or
    None (account level, or an existing transaction: `transaction_id`)."""

    code: str
    date: date
    account_key: str | None
    line: str | None
    transaction_id: UUID | None
    introduced: bool


@dataclass(frozen=True, slots=True)
class Preview:
    source: str
    file_name: str | None
    file_sha256: str
    move_cash: bool
    account_code: str | None
    accounts: tuple[AccountPlan, ...]
    assets: tuple[AssetPlan, ...]
    lines: tuple[Line, ...]
    notices: tuple[Reason, ...]
    blocking: tuple[Reason, ...]  # to solve before confirming (choices, quantity conflict)
    warnings: tuple[ImportWarning, ...]
    fingerprint: str

    def count(self, category: Category) -> int:
        return sum(1 for line in self.lines if line.candidate.category is category)

    @property
    def selected_count(self) -> int:
        return sum(1 for line in self.lines if line.selected)


# --- Preview ----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Work:
    """The preview plus what confirmation needs to write it."""

    preview: Preview
    ids: dict[str, UUID]  # line key -> id of the transaction to create


def preview(
    db: Session,
    owner_id: UUID,
    inp: ImportInput,
    *,
    today: date,
    parsed: ParsedImport | None = None,
) -> Preview:
    """Everything the import would do, without writing anything (D-100). Raises ApiError when
    the file cannot be read (413/422), a choice is invalid (422) or names an account or asset
    the user cannot use (404)."""
    return _build(db, owner_id, inp, today, parsed).preview


def _build(
    db: Session,
    owner_id: UUID,
    inp: ImportInput,
    today: date,
    parsed: ParsedImport | None = None,
    expected: str | None = None,
) -> _Work:
    """`parsed`: the result of `parse(inp)`, when the caller parsed the file before taking
    the write lock (parsing is pure: the same file and inputs give the same result).
    `expected`: the fingerprint the user saw (confirmation). It is compared as soon as it is
    known, before the selection is checked: a stale preview is always IMPORT_PREVIEW_STALE,
    even when a line the user ticked has become a duplicate meanwhile."""
    parsed = parsed if parsed is not None else parse(inp)
    notices = list(parsed.notices)
    if parsed.source == DIRECTA:
        plans, directa_notices = _directa_account(db, owner_id, inp, parsed)
        notices += directa_notices
    else:
        plans = _csv_accounts(db, owner_id, inp, parsed)
    plan_of = {p.key: p for p in plans}
    lines = list(parsed.candidates)
    account_key = _account_keys(parsed, lines)
    ledgers = {p.key: txs.account_ledger(db, owner_id, p.account_id) for p in plans if not p.new}
    ids: dict[str, UUID] = {}
    lines = _with_duplicates(db, owner_id, parsed.source, lines, account_key, ledgers, ids)
    lines = [_check_account_currency(c, plan_of.get(account_key[c.key])) for c in lines]

    asset_plans, asset_notices, asset_blockers = _asset_plans(
        db, owner_id, inp, parsed.source, lines
    )
    notices += asset_notices
    asset_key = {c.key: _asset_key(c.asset) for c in lines if c.selectable and c.asset}
    engine_assets = {
        p.key: _engine_asset(p) for p in asset_plans if p.complete and not asset_blockers[p.key]
    }
    lines = [_checked(c, parsed.source, engine_assets.get(asset_key.get(c.key, ""))) for c in lines]
    notices += _manual_overlaps(parsed, plans, ledgers)

    file_sha256 = hashlib.sha256(inp.content).hexdigest()
    fingerprint = _fingerprint(
        {
            "source": parsed.source, "file": file_sha256, "file_name": inp.file_name,
            "move_cash": inp.move_cash, "accounts": plans, "assets": asset_plans,
            "lines": [(c, account_key[c.key], asset_key.get(c.key)) for c in lines],
            "notices": notices,
            "ledgers": {
                str(key): [(r.id, r.sequence, r.updated_at) for r in rows]
                for key, rows in ledgers.items()
            },
            "owner": _owner_state(db, owner_id),
        }
    )  # fmt: skip
    if expected is not None and not hmac.compare_digest(fingerprint, expected):
        raise ApiError(
            409, "IMPORT_PREVIEW_STALE", "the file or your data changed since the preview"
        )

    selected = _selection(lines, inp.selected)
    lines = _with_related_orders(lines, selected)

    used_accounts = {account_key[c.key] for c in lines if c.key in selected}
    used_assets = {asset_key[c.key] for c in lines if c.key in selected and c.key in asset_key}
    blocking = [
        reason("ACCOUNT_CURRENCY_REQUIRED", account=p.key)
        for p in plans
        if p.new and p.currency is None and p.key in used_accounts
    ]
    for plan in asset_plans:
        if plan.key in used_assets:
            blocking += asset_blockers[plan.key]

    warnings: list[ImportWarning] = []
    if not blocking:
        conflict, warnings = _dry_run(
            db, plans, lines, selected, account_key, asset_key, engine_assets, ledgers, today
        )
        blocking += conflict

    result = Preview(
        source=parsed.source,
        file_name=inp.file_name,
        file_sha256=file_sha256,
        move_cash=inp.move_cash,
        account_code=parsed.account_code,
        accounts=tuple(plans),
        assets=tuple(asset_plans),
        lines=tuple(
            Line(c, account_key[c.key], asset_key.get(c.key), c.key in selected) for c in lines
        ),
        notices=tuple(notices),
        blocking=tuple(blocking),
        warnings=tuple(warnings),
        fingerprint=fingerprint,
    )
    return _Work(result, ids)


def decode(content_base64: str) -> bytes:
    """The uploaded file (D-101: the length is checked before decoding)."""
    try:
        return decode_upload(content_base64)
    except ImportFileError as exc:
        error = file_error(exc)
    raise error  # outside `except`: no trace of the original exception (see `parse`)


def parse(inp: ImportInput) -> ParsedImport:
    """Parse the untrusted file. The cause of a failure can quote a cell, i.e. personal data
    (a name, an amount): it is never logged nor kept. The API error is raised outside the
    `except` block, so it has neither `__cause__` nor `__context__` for a traceback to print,
    and an unexpected failure (a bug) is reported by its exception type only."""
    try:
        importer = importer_for(inp.content, inp.source)
        return importer.parse(inp.content, move_cash=inp.move_cash)
    except ImportFileError as exc:
        error: Exception = file_error(exc)
    except Exception as exc:  # re-raised below, without its message
        kind = type(exc).__name__
        log.error("import: the file parser failed", extra={"exception_type": kind})
        error = RuntimeError(f"import: the file parser failed ({kind})")
    raise error


def file_error(exc: ImportFileError) -> ApiError:
    """The API error of a file that cannot be read: its stable code, developer hint and
    parameters only (the hint never quotes the file)."""
    status = 413 if exc.code == "IMPORT_TOO_LARGE" else 422
    return ApiError(status, exc.code, exc.detail, **exc.params)


def _choice_invalid(detail: str, **params: str) -> ApiError:
    return ApiError(422, "IMPORT_CHOICE_INVALID", detail, **params)


# --- Confirm ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Confirmed:
    batches: tuple[models.ImportBatch, ...]  # one per target account, in file order
    created: tuple[models.Transaction, ...]
    accounts_created: tuple[UUID, ...]
    assets_created: tuple[UUID, ...]
    warnings: tuple[ImportWarning, ...]  # those of the preview: the same replay


def confirm(
    db: Session,
    owner_id: UUID,
    actor_id: UUID,
    inp: ImportInput,
    fingerprint: str,
    *,
    today: date,
    ip: str | None = None,
    parsed: ParsedImport | None = None,
) -> Confirmed:
    """Import the selected lines exactly as previewed (hard rule 6). Must run inside the
    request's write transaction (BEGIN IMMEDIATE). Raises ApiError, with nothing written:
    409 IMPORT_PREVIEW_STALE when anything shown changed since the preview, 409
    QUANTITY_CONFLICT, 422 IMPORT_NOT_READY while a choice is missing, and every error of
    `preview`. An account to create gets no batch when none of its lines is selected.
    `parsed` as for `preview`: parse first, so the write lock is not held while parsing."""
    work = _build(db, owner_id, inp, today, parsed, expected=fingerprint)
    shown = work.preview
    for block in shown.blocking:
        if block.code == "QUANTITY_CONFLICT":
            raise ApiError(
                409,
                "QUANTITY_CONFLICT",
                "the import would leave an invalid quantity",
                conflict=block.as_dict(),
            )
    if shown.blocking:
        raise ApiError(
            422,
            "IMPORT_NOT_READY",
            "choose what the preview asks for before confirming",
            blocking=[{"code": r.code, **r.as_dict()} for r in shown.blocking],
        )

    chosen = creation_order(shown.lines)
    account_ids, accounts_created, remembered = _target_accounts(db, owner_id, shown, chosen)
    asset_ids, assets_created = _target_assets(db, owner_id, shown, chosen)
    made = {}
    for key, account_id in account_ids.items():
        lines = [line for line in shown.lines if line.account_key == key]
        made[key] = batches.add_batch(
            db, owner_id, account_id, source=shown.source, file_name=shown.file_name,
            file_sha256=shown.file_sha256, move_cash=shown.move_cash, **_counts(lines),
        )  # fmt: skip
    result = apply_change(
        db,
        owner_id,
        actor_id,
        creates=[
            tx_data(line.candidate, account_ids[line.account_key], asset_ids.get(line.asset_key))
            for line in chosen
        ],
        new_ids=[work.ids[line.key] for line in chosen],
        import_batches={account_ids[key]: batch.id for key, batch in made.items()},
        audit_each=False,
        today=today,
        ip=ip,
    )
    for key, batch in made.items():
        used = {line.asset_key for line in chosen if line.account_key == key}
        audit.record(
            db, "import.confirmed", actor_id=actor_id, entity_type="import_batch",
            entity_id=batch.id, ip=ip,
            after={
                "account_id": batch.account_id, "source": batch.source,
                "file_sha256": batch.file_sha256, "move_cash": batch.move_cash,
                **_counts([line for line in shown.lines if line.account_key == key]),
                "account_created": batch.account_id in accounts_created,
                "account_code_remembered": remembered,
                "assets_created": [aid for k, aid in assets_created.items() if k in used],
            },
        )  # fmt: skip
    return Confirmed(
        batches=tuple(made.values()),
        created=tuple(result.created),
        accounts_created=tuple(accounts_created),
        assets_created=tuple(assets_created.values()),
        warnings=shown.warnings,
    )


def _target_accounts(
    db: Session, owner_id: UUID, shown: Preview, chosen: list[Line]
) -> tuple[dict[str | None, UUID], list[UUID], bool]:
    """The account of every plan, creating those the selected lines need; a Directa
    account remembers the file's account code when it has none (directa.md §4)."""
    used = {line.account_key for line in chosen}
    ids: dict[str | None, UUID] = {}
    created: list[UUID] = []
    for plan in shown.accounts:
        if not plan.new:
            ids[plan.key] = plan.account_id
        elif plan.key in used:
            row = accounts.add_account(
                db, owner_id, name=plan.name, currency=plan.currency, provider=plan.provider
            )
            ids[plan.key] = row.id
            created.append(row.id)
    remembered = False
    if shown.source == DIRECTA and shown.account_code:
        row = accounts.get_account(db, owner_id, shown.accounts[0].account_id)
        if row is not None and row.external_code is None:
            row.external_code = shown.account_code
            db.flush()
            remembered = True
    return ids, created, remembered


def _target_assets(
    db: Session, owner_id: UUID, shown: Preview, chosen: list[Line]
) -> tuple[dict[str, UUID], dict[str, UUID]]:
    """The asset of every plan, creating the new private assets the selected lines use."""
    used = {line.asset_key for line in chosen if line.asset_key}
    ids: dict[str, UUID] = {}
    created: dict[str, UUID] = {}
    for plan in shown.assets:
        if not plan.new:
            ids[plan.key] = plan.asset_id
        elif plan.key in used:
            row = assets.add_private_asset(
                db, owner_id, name=plan.name, isin=plan.isin, ticker=plan.ticker,
                asset_type=plan.asset_type.value, quote_currency=plan.quote_currency,
                quote_unit=plan.quote_unit.value,
            )  # fmt: skip
            ids[plan.key] = created[plan.key] = row.id
    return ids, created


def _counts(lines: list[Line]) -> dict[str, int]:
    """The import batch counts of one account's lines (domain-model "Import Batch")."""

    def count(*categories: Category, selected: bool = False) -> int:
        return sum(
            1 for x in lines if x.candidate.category in categories and x.selected is selected
        )

    c = Category
    return {
        "imported": sum(1 for x in lines if x.selected),
        "duplicates": count(c.DUPLICATE),
        "not_recognized": count(c.NOT_RECOGNIZED),
        "to_review_skipped": count(c.TO_REVIEW),
        "ambiguous": count(c.AMBIGUOUS),
        "invalid": count(c.INVALID),
        "skipped_by_choice": count(c.TO_IMPORT, c.SKIPPED_BY_CHOICE),
    }


# --- Accounts (directa.md §4, app-csv.md "Accounts") --------------------------------------------


def _directa_account(
    db: Session, owner_id: UUID, inp: ImportInput, parsed: ParsedImport
) -> tuple[list[AccountPlan], list[Reason]]:
    if inp.accounts:
        raise _choice_invalid("a Directa file has one account: choose it with account_id")
    code = parsed.account_code
    if inp.account_id is not None:
        account = accounts.get_account(db, owner_id, inp.account_id)
        if account is None:
            raise not_found("account")
    else:  # the account that remembers this account code (§4)
        known = [
            a for a in accounts.list_accounts(db, owner_id) if code and a.external_code == code
        ]
        if len(known) != 1:
            raise ApiError(422, "IMPORT_ACCOUNT_REQUIRED", "choose the account to import into")
        account = known[0]
    if account.currency != "EUR":  # every verified Directa row is in EUR (D-49)
        raise ApiError(
            422,
            "IMPORT_ACCOUNT_CURRENCY",
            "a Directa file can be imported only into an EUR account",
            account_currency=account.currency,
        )
    notices = []
    if code and account.external_code and account.external_code != code:
        notices.append(
            reason("ACCOUNT_CODE_DIFFERS", file_code=code, account_code=account.external_code)
        )
    plan = AccountPlan(None, account.id, account.name, account.currency, account.provider)
    return [plan], notices


def _csv_accounts(
    db: Session, owner_id: UUID, inp: ImportInput, parsed: ParsedImport
) -> list[AccountPlan]:
    if inp.account_id is not None:
        raise _choice_invalid("an app CSV names its accounts: map them with accounts")
    refs: dict[str, list] = defaultdict(list)
    for c in parsed.candidates:
        if c.account is not None:
            refs[c.account.name].append(c.account)
    unknown = sorted(set(inp.accounts) - set(refs))
    if unknown:
        raise _choice_invalid("the file has no account with this name", account=unknown[0])
    existing = accounts.list_accounts(db, owner_id)
    plans = []
    for name, found in refs.items():
        currency = next((r.currency for r in found if r.currency), None)
        provider = next((r.provider for r in found if r.provider), None)
        target: models.Account | None = None
        if name in inp.accounts:
            if inp.accounts[name] is not None:
                target = accounts.get_account(db, owner_id, inp.accounts[name])
                if target is None:
                    raise not_found("account")
        else:  # proposal: an account with the same name and currency (oldest first)
            same = [a for a in existing if a.name == name and currency in (None, a.currency)]
            target = same[0] if same else None
        if target is None:
            plans.append(AccountPlan(name, None, name, currency, provider))
        else:
            plans.append(
                AccountPlan(name, target.id, target.name, target.currency, target.provider)
            )
    return plans


def _account_keys(parsed: ParsedImport, lines: list[Candidate]) -> dict[str, str | None]:
    """The account of every line. A CSV line whose account cell cannot be read is counted in
    the batch of the file's first account."""
    if parsed.source == DIRECTA:
        return {c.key: None for c in lines}
    first = next((c.account.name for c in lines if c.account), None)
    return {c.key: c.account.name if c.account else first for c in lines}


def _check_account_currency(c: Candidate, plan: AccountPlan | None) -> Candidate:
    """Amounts are in the account currency (D-98): a CSV row naming another currency than its
    target account's is refused."""
    if not c.selectable or c.account is None or plan is None:
        return c
    if c.account.currency and plan.currency and c.account.currency != plan.currency:
        return _reject(
            c,
            Category.INVALID,
            reason("ACCOUNT_CURRENCY_MISMATCH", currency=c.account.currency,
                   account_currency=plan.currency),
        )  # fmt: skip
    return c


def _manual_overlaps(
    parsed: ParsedImport, plans: list[AccountPlan], ledgers: Mapping[str | None, list]
) -> list[Reason]:
    """D-104: transactions without `external_ref` (entered by hand) inside the file's dates."""
    if parsed.first_date is None or parsed.last_date is None:
        return []
    out = []
    for plan in plans:
        manual = [
            r
            for r in ledgers.get(plan.key, ())
            if r.external_ref is None and parsed.first_date <= r.trade_date <= parsed.last_date
        ]
        if manual:
            out.append(
                reason("MANUAL_OVERLAP", account=plan.key or "", count=len(manual),
                       first_date=parsed.first_date, last_date=parsed.last_date)
            )  # fmt: skip
    return out


# --- Duplicates (D-38, directa.md §6) -----------------------------------------------------------


def _with_duplicates(
    db: Session,
    owner_id: UUID,
    source: str,
    lines: list[Candidate],
    account_key: Mapping[str, str | None],
    ledgers: Mapping[str | None, list[models.Transaction]],
    ids: dict[str, UUID],
) -> list[Candidate]:
    """Mark what is already present and choose the id of every transaction to create: a CSV
    row keeps its `id` unless another user's transaction has it (then a new one, silently)."""
    refs = {
        key: {(r.external_ref, r.type) for r in rows if r.external_ref}
        for key, rows in ledgers.items()
    }
    file_ids = {c.id for c in lines if c.selectable and c.id is not None}
    own = {r.id for r in txs.get_transactions(db, owner_id, file_ids)}
    taken = txs.ids_in_use(db, file_ids - own)
    out = []
    for c in lines:
        if not c.selectable:
            out.append(c)
            continue
        if c.id is not None and c.id in own:
            out.append(_reject(c, Category.DUPLICATE, reason("ALREADY_PRESENT", match="id")))
            continue
        if c.external_ref and (c.external_ref, c.type.value) in refs.get(account_key[c.key], ()):
            match = "external_ref" if source == APP_CSV else "external_ref_and_type"
            out.append(_reject(c, Category.DUPLICATE, reason("ALREADY_PRESENT", match=match)))
            continue
        ids[c.key] = c.id if c.id is not None and c.id not in taken else uuid.uuid4()
        out.append(c)
    return out


# --- Assets (D-102, D-111, directa.md §5, app-csv.md "Assets") --------------------------------


def _asset_key(ref: AssetRef) -> str | None:
    if ref.isin:
        return f"isin:{ref.isin}"
    if ref.ticker:
        return f"ticker:{ref.ticker}"
    if ref.name:
        return f"name:{ref.name}"
    return None


def _asset_plans(
    db: Session, owner_id: UUID, inp: ImportInput, source: str, lines: list[Candidate]
) -> tuple[list[AssetPlan], list[Reason], dict[str, list[Reason]]]:
    """One plan per asset of the lines still to import, in file order, with its notices and
    what blocks the confirmation until the user chooses (D-102). A choice for an asset that
    needs none (already present, or no line left to import) is ignored: a preview made
    meanwhile may have changed that, and the fingerprint covers the outcome."""
    refs: dict[str, list[AssetRef]] = {}
    for c in lines:
        if c.selectable and c.asset is not None and (key := _asset_key(c.asset)):
            refs.setdefault(key, []).append(c.asset)
    visible = assets.list_assets(db, owner_id)
    by_isin: dict[str, models.Asset] = {}
    for a in sorted(visible, key=lambda a: a.owner_id is None):  # private first
        if a.isin:
            by_isin.setdefault(a.isin, a)
    plans, notices, blockers = [], [], {}
    for key, found in refs.items():
        ref = _merged(found)
        choice = inp.assets.get(key)
        options: tuple[UUID, ...] = ()
        existing = by_isin.get(ref.isin) if ref.isin else None
        if existing is not None:
            choice = None  # an ISIN identifies the asset: nothing to choose
        elif choice is not None and choice.asset_id is not None:
            if ref.isin:
                raise _choice_invalid("an asset with an ISIN cannot be mapped", asset=key)
            existing = assets.get_asset(db, owner_id, choice.asset_id)
            if existing is None:
                raise not_found("asset")
        if ref.isin is None and ref.ticker:
            options = tuple(a.id for a in visible if a.ticker == ref.ticker)
            if existing is None and choice is None and len(options) == 1:
                existing = next(a for a in visible if a.id == options[0])
        if existing is not None:
            plans.append(_existing_plan(key, existing, options))
            blockers[key] = []
            if source == APP_CSV:
                notices += _definition_differs(key, ref, plans[-1])
        elif len(options) > 1 and choice is None:  # several assets have this ticker
            plans.append(_new_plan(key, ref, None, options))
            blockers[key] = [reason("ASSET_CHOICE_REQUIRED", asset=key)]
        else:
            plans.append(_new_plan(key, ref, choice, options))
            blockers[key] = _new_asset_blockers(plans[-1])
    return plans, notices, blockers


def _merged(refs: list[AssetRef]) -> AssetRef:
    """The first value the file gives for each field, over all rows of the asset."""
    return AssetRef(
        **{f.name: next((getattr(r, f.name) for r in refs if getattr(r, f.name)), None)
           for f in fields(AssetRef)}
    )  # fmt: skip


def _existing_plan(key: str, asset: models.Asset, options: tuple[UUID, ...]) -> AssetPlan:
    return AssetPlan(
        key=key,
        asset_id=asset.id,
        name=asset.name,
        isin=asset.isin,
        ticker=asset.ticker,
        asset_type=AssetType(asset.asset_type),
        quote_currency=asset.quote_currency,
        quote_unit=QuoteUnit(asset.quote_unit),
        options=options,
    )


def _new_plan(
    key: str, ref: AssetRef, choice: AssetChoice | None, options: tuple[UUID, ...]
) -> AssetPlan:
    choice = choice or AssetChoice()
    asset_type = choice.asset_type or ref.asset_type
    unit = choice.quote_unit
    if unit is None and asset_type is not None:
        # The file's unit when it fits the type (e.g. GBX for a stock), else the type's.
        fits = ref.quote_unit is not None and (
            (asset_type is AssetType.BOND) == (ref.quote_unit is QuoteUnit.PERCENT_OF_NOMINAL)
        )
        unit = ref.quote_unit if fits else _unit_for(asset_type)
    return AssetPlan(
        key=key,
        asset_id=None,
        name=ref.name or ref.ticker or ref.isin or "",
        isin=ref.isin,
        ticker=ref.ticker,
        asset_type=asset_type,
        quote_currency=choice.quote_currency or ref.quote_currency,
        quote_unit=unit,
        options=options,
    )


def _unit_for(asset_type: AssetType) -> QuoteUnit:
    """D-102: bonds are quoted as % of nominal, everything else in currency."""
    return QuoteUnit.PERCENT_OF_NOMINAL if asset_type is AssetType.BOND else QuoteUnit.CURRENCY


def _definition_differs(key: str, ref: AssetRef, plan: AssetPlan) -> list[Reason]:
    """D-111: the CSV asset columns are ignored for an existing asset; a mismatch is noted."""
    return [
        reason("ASSET_DEFINITION_DIFFERS", asset=key, field=name)
        for name in ("asset_type", "quote_currency", "quote_unit")
        if getattr(ref, name) is not None and getattr(ref, name) != getattr(plan, name)
    ]


def _new_asset_blockers(plan: AssetPlan) -> list[Reason]:
    out = []
    if plan.asset_type is None:
        out.append(reason("ASSET_TYPE_REQUIRED", asset=plan.key))
    if plan.quote_currency is None:
        out.append(reason("ASSET_CURRENCY_REQUIRED", asset=plan.key))
    if plan.complete:
        out += [
            reason("ASSET_INVALID", asset=plan.key, field=i.field, rule=i.code)
            for i in validate_asset(_engine_asset(plan))
        ]
    return out


def _engine_asset(plan: AssetPlan) -> engine.Asset:
    """The asset as the engine sees it; a new asset gets an id derived from its key."""
    return engine.Asset(
        id=plan.asset_id or uuid.uuid5(uuid.NAMESPACE_URL, f"import-asset:{plan.key}"),
        asset_type=plan.asset_type,
        quote_currency=plan.quote_currency,
        quote_unit=plan.quote_unit,
    )


# --- Lines ------------------------------------------------------------------------------------


def _reject(c: Candidate, category: Category, *reasons: Reason) -> Candidate:
    return replace(c, category=category, reasons=c.reasons + reasons)


def tx_data(c: Candidate, account_id: UUID, asset_id: UUID | None) -> TxData:
    """The transaction a selectable line creates."""
    if c.type is None or c.trade_date is None:
        raise RuntimeError("import: a selectable line has no type or date")
    return TxData(
        account_id=account_id,
        type=c.type,
        trade_date=c.trade_date,
        trade_time=c.trade_time,
        value_date=c.value_date,
        asset_id=asset_id,
        quantity=c.quantity,
        unit_price=c.unit_price,
        gross_amount=c.gross_amount,
        fees=c.fees,
        accrued_interest_paid=c.accrued_interest_paid,
        accrued_interest_received=c.accrued_interest_received,
        taxes=c.taxes,
        total_amount=c.total_amount,
        split_ratio=c.split_ratio,
        affects_cash=c.affects_cash,
        external_ref=c.external_ref,
        notes=c.notes,
        metadata=dict(c.metadata) if c.metadata is not None else None,
    )


_NO_ACCOUNT = uuid.UUID(int=0)  # field rules do not depend on the account


def _checked(c: Candidate, source: str, asset: engine.Asset | None) -> Candidate:
    """The API's limits and the engine's field rules (domain-rules §2) on a line still to
    import: a line that breaks one is invalid before the user confirms anything. A line whose
    new asset has no type yet is checked again once the type is chosen."""
    if not c.selectable:
        return c
    if source == DIRECTA and asset is not None:  # Directa gives no unit price (§3)
        c = replace(c, unit_price=derived_unit_price(c, is_bond=asset.is_bond))
    beyond = _beyond_limits(c)
    if beyond:
        return _reject(c, Category.INVALID, *beyond)
    if c.asset is not None and asset is None:
        return c
    tx = tx_data(c, _NO_ACCOUNT, asset.id if asset else None).engine_tx(_NO_ACCOUNT, 1)
    issues = validate_transaction(tx, asset)
    if issues:
        return _reject(
            c,
            Category.INVALID,
            *(reason("FIELD_RULE", field=i.field, rule=i.code) for i in issues),
        )
    return c


# What the API and the database accept (D-98; api/routes/transactions.py and assets.py):
# (field shown to the user, attribute, maximum length).
TEXT_LIMITS = (("external_ref", "external_ref", 64), ("notes", "notes", 2000))
ASSET_TEXT_LIMITS = (("ticker", "ticker", 32), ("asset_name", "name", 200))
METADATA_LIMIT = 10_000  # characters of its JSON text, as for the app CSV


def _beyond_limits(c: Candidate) -> list[Reason]:
    """The limits a value typed in the API would meet, also on what an importer computed:
    a Directa order sums its executions and derives its unit price, which no cell check sees
    (e.g. a quantity of 0.00000001 for a large amount). Such a line is refused, never cut or
    rounded."""
    out = [
        reason("FIELD_INVALID", field=name, problem="OUT_OF_RANGE")
        for name in engine.DECIMAL_FIELDS
        if (value := getattr(c, name)) is not None
        and not textformats.DECIMAL.fullmatch(canonical_decimal(value))
    ]
    texts = [(field, getattr(c, attr), n) for field, attr, n in TEXT_LIMITS]
    if c.asset is not None:
        texts += [(field, getattr(c.asset, attr), n) for field, attr, n in ASSET_TEXT_LIMITS]
    out += [
        reason("FIELD_INVALID", field=field, problem="TOO_LONG")
        for field, value, limit in texts
        if value is not None and len(value) > limit
    ]
    if c.metadata is not None and len(json.dumps(dict(c.metadata))) > METADATA_LIMIT:
        out.append(reason("FIELD_INVALID", field="metadata", problem="TOO_LONG"))
    return out


def _selection(lines: list[Candidate], chosen: frozenset[str] | None) -> frozenset[str]:
    if chosen is None:
        return frozenset(c.key for c in lines if c.selected_by_default)
    selectable = {c.key for c in lines if c.selectable}
    wrong = sorted(chosen - selectable)
    if wrong:
        raise ApiError(
            422,
            "IMPORT_SELECTION_INVALID",
            "only lines that can be imported can be selected",
            lines=wrong[:20],
        )
    return frozenset(chosen)


def _with_related_orders(lines: list[Candidate], selected: frozenset[str]) -> list[Candidate]:
    """D-99 note: a selected income or sale whose asset's purchase in this file is still to
    review and not selected (`RELATED_ORDER_TO_REVIEW`). Information only."""
    pending: dict[str, list[Candidate]] = defaultdict(list)
    for c in lines:
        to_review = c.category is Category.TO_REVIEW and c.type is T.BUY
        if to_review and c.key not in selected and c.asset is not None and c.asset.isin:
            pending[c.asset.isin].append(c)
    if not pending:
        return lines
    out = []
    for c in lines:
        orders = pending.get(c.asset.isin) if c.asset and c.asset.isin else None
        if c.key in selected and c.type in _RELATED_TO_PURCHASE and orders:
            notes = tuple(
                reason(
                    "RELATED_ORDER_TO_REVIEW", order_key=o.key, external_ref=o.external_ref or ""
                )
                for o in orders
            )
            c = replace(c, info=c.info + notes)
        out.append(c)
    return out


# --- Dry run (domain-rules §4) ----------------------------------------------------------------


def creation_order(lines: Iterable[Line]) -> list[Line]:
    """Selected lines in insertion order: the importer's sequence (D-70, Directa §8). Each
    account then numbers its own transactions after its existing ones."""
    return sorted(
        (line for line in lines if line.selected), key=lambda line: line.candidate.sequence
    )


def _dry_run(
    db: Session,
    plans: list[AccountPlan],
    lines: list[Candidate],
    selected: frozenset[str],
    account_key: Mapping[str, str | None],
    asset_key: Mapping[str, str],
    engine_assets: Mapping[str, engine.Asset],
    ledgers: Mapping[str | None, list[models.Transaction]],
    today: date,
) -> tuple[list[Reason], list[ImportWarning]]:
    """Replay every affected ledger with the selected lines, as confirmation will."""
    chosen = creation_order(
        Line(c, account_key[c.key], asset_key.get(c.key), c.key in selected) for c in lines
    )
    by_account: dict[str | None, list[Line]] = defaultdict(list)
    for line in chosen:
        by_account[line.account_key].append(line)
    known = {a.id: a for a in engine_assets.values()}
    blocking: list[Reason] = []
    warnings: list[ImportWarning] = []
    for plan in plans:
        new = by_account.get(plan.key)
        if not new:
            continue
        current = [mapping.to_engine_transaction(r) for r in ledgers.get(plan.key, ())]
        stored = {t.asset_id for t in current if t.asset_id} - known.keys()
        known |= {
            aid: mapping.to_engine_asset(a) for aid, a in assets.assets_by_id(db, stored).items()
        }
        account_id = plan.account_id or _NO_ACCOUNT
        next_seq = max((t.sequence for t in current), default=0) + 1
        line_of: dict[UUID, str] = {}
        upsert = []
        for offset, line in enumerate(new):
            tx_id = uuid.uuid5(uuid.NAMESPACE_URL, f"import-line:{line.key}")
            line_of[tx_id] = line.key
            asset = engine_assets.get(line.asset_key or "")
            data = tx_data(line.candidate, account_id, asset.id if asset else None)
            upsert.append(data.engine_tx(tx_id, next_seq + offset))
        outcome = edits.apply_changes(current, known, upsert=upsert)
        if outcome.field_issues:  # every line passed `_checked`: a bug if this happens
            raise RuntimeError("import: a checked line breaks a field rule in the dry run")
        if (conflict := outcome.conflict) is not None:
            where = line_of.get(conflict.transaction_id)
            blocking.append(
                reason("QUANTITY_CONFLICT", reason=conflict.code.value, account=plan.key or "",
                       line=where or "", quantity=canonical_decimal(conflict.quantity),
                       transaction_id="" if where else conflict.transaction_id)
            )  # fmt: skip
            continue
        for w in change_warnings(account_id, current, outcome, today):
            where = line_of.get(w.transaction_id) if w.transaction_id else None
            warnings.append(
                ImportWarning(w.code, w.date, plan.key, where,
                              None if where else w.transaction_id, w.introduced)
            )  # fmt: skip
    return blocking, warnings


# --- Fingerprint (D-100) ----------------------------------------------------------------------


def _fingerprint(state: dict[str, Any]) -> str:
    """SHA-256 of what the user is shown, except what depends on the selection (the dry run,
    warnings, blocking choices, notes about related orders), plus the state of each target
    ledger and of the user's accounts and imports (D-100, D-114): a second confirmation of
    the same preview is stale even when it would create its accounts again."""
    lines = [
        {**{f.name: _plain(getattr(c, f.name)) for f in fields(Candidate)},
         "account": account, "asset": asset}
        for c, account, asset in state.pop("lines")
    ]  # fmt: skip
    text = json.dumps(
        audit.jsonable(_plain(state | {"lines": lines})), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode()).hexdigest()


def _owner_state(db: Session, owner_id: UUID) -> list[str]:
    """The user's account ids and newest import batch: any account created or import
    confirmed since the preview makes it stale."""
    ids = sorted(str(a.id) for a in accounts.list_accounts(db, owner_id))
    latest = batches.latest_batch(db, owner_id)
    return [*ids, f"batch:{latest.id if latest else ''}"]


def _plain(value: Any) -> Any:
    if isinstance(value, Reason):
        return [value.code, value.as_dict()]
    if hasattr(value, "__dataclass_fields__"):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return value
