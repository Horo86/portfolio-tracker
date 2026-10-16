"""Group Directa rows into transactions (`docs/import/directa.md` §2-§3, §7, §8).

Pure: the result refers to assets by ISIN; the import service resolves them, finds the
duplicates already stored and validates the batch with the engine. Every row ends up in
exactly one entry, in one category:

- TO_IMPORT: verified and consistent, selected by default;
- TO_REVIEW: mapped, but containing a movement type whose meaning is not confirmed (D-99):
  not selected by default, selectable;
- SKIPPED_BY_CHOICE: a deposit or account expense skipped by the "do not move cash" option
  (D-49): not selected by default, selectable;
- AMBIGUOUS: inconsistent rows (D-108, D-105, D-106): never imported;
- NOT_RECOGNIZED: a movement type or shape that is not verified (D-20): never imported.

Reasons and notes are stable codes with parameters; the UI translates them.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from enum import Enum, StrEnum
from types import MappingProxyType

from portfolio_tracker.engine.decimals import div8
from portfolio_tracker.engine.model import TransactionType
from portfolio_tracker.importers.directa_reader import DirectaFile, DirectaRow

T = TransactionType
ZERO = Decimal(0)


class Category(StrEnum):
    TO_IMPORT = "TO_IMPORT"
    TO_REVIEW = "TO_REVIEW"
    SKIPPED_BY_CHOICE = "SKIPPED_BY_CHOICE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_RECOGNIZED = "NOT_RECOGNIZED"


SELECTABLE = frozenset({Category.TO_IMPORT, Category.TO_REVIEW, Category.SKIPPED_BY_CHOICE})


class Kind(Enum):
    BUY = "BUY"  # main row of an order
    FEES = "FEES"
    ACCRUED = "ACCRUED"
    TAX_CREDIT = "TAX_CREDIT"
    COUPON = "COUPON"
    COUPON_TAX = "COUPON_TAX"
    DIVIDEND = "DIVIDEND"
    DIVIDEND_TAX = "DIVIDEND_TAX"
    DEPOSIT = "DEPOSIT"
    EXPENSE = "EXPENSE"


# The verified movement types (§2), matched exactly; the `*` is a literal character (D-49).
VERIFIED: Mapping[str, Kind] = MappingProxyType(
    {
        "Acquisto": Kind.BUY,
        "Commissioni": Kind.FEES,
        "Ratei pass.obb.": Kind.ACCRUED,
        "Rit.ratei pass.obb.": Kind.TAX_CREDIT,
        "Rit.credito disaggio": Kind.TAX_CREDIT,
        "Cedola obb.": Kind.COUPON,
        "Rit.cedola obb.": Kind.COUPON_TAX,
        "Incasso dividendi italia": Kind.DIVIDEND,
        "Ritenuta dividendi italia": Kind.DIVIDEND_TAX,
        "Conferimento con bonifico": Kind.DEPOSIT,
        "Bollo portafoglio titoli*": Kind.EXPENSE,
        "Tobin tax italia": Kind.EXPENSE,
    }
)
# Mapped as documented but not confirmed by the broker: the transaction is "to review" (D-99).
UNCONFIRMED: Mapping[str, str] = MappingProxyType({"Rit.credito disaggio": "TAX_CREDIT"})
# Rows that prove an ISIN is a bond (D-102), besides a ticker starting with "M." (§1).
BOND_ROWS = frozenset({"Cedola obb.", "Ratei pass.obb.", "Rit.ratei pass.obb."})

_ORDER_KINDS = frozenset({Kind.BUY, Kind.FEES, Kind.ACCRUED, Kind.TAX_CREDIT})
_INCOME = {Kind.COUPON: (T.COUPON, Kind.COUPON_TAX), Kind.DIVIDEND: (T.DIVIDEND, Kind.DIVIDEND_TAX)}
_WITHHOLDINGS = frozenset({Kind.COUPON_TAX, Kind.DIVIDEND_TAX})


@dataclass(frozen=True, slots=True)
class Reason:
    """A stable code plus parameters (strings), translated by the UI."""

    code: str
    params: tuple[tuple[str, str], ...] = ()  # sorted (name, value) pairs: hashable

    def as_dict(self) -> dict[str, str]:
        return dict(self.params)


def _reason(code: str, **params: object) -> Reason:
    return Reason(code, tuple(sorted((k, str(v)) for k, v in params.items())))


@dataclass(frozen=True, slots=True)
class Entry:
    """One preview line: a mapped transaction (selectable categories) or rejected rows."""

    key: str  # stable across preview and confirm of the same file (D-100)
    category: Category
    lines: tuple[int, ...]  # sheet rows, as in the file
    reasons: tuple[Reason, ...] = ()  # why it is to review / ambiguous / not recognized
    info: tuple[Reason, ...] = ()  # informational (merged executions, pairing, ...)
    type: TransactionType | None = None
    trade_date: date | None = None
    value_date: date | None = None
    isin: str | None = None
    ticker: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    gross_amount: Decimal | None = None
    fees: Decimal | None = None
    accrued_interest_paid: Decimal | None = None
    taxes: Decimal | None = None
    total_amount: Decimal | None = None
    affects_cash: bool = True
    external_ref: str | None = None
    notes: str | None = None  # the transaction's `notes` field
    metadata: Mapping[str, str] | None = None
    account_expense: bool = False  # stamp duty / Tobin tax (grouped in the preview, §7)
    sequence: int | None = None  # 1.. in chronological file order (§8), selectable only

    @property
    def selectable(self) -> bool:
        return self.category in SELECTABLE

    @property
    def selected_by_default(self) -> bool:
        return self.category is Category.TO_IMPORT

    def unit_price(self, is_bond: bool) -> Decimal | None:
        """BUY unit price in the asset quote unit: per unit, or % of nominal for bonds (§3)."""
        if self.type is not T.BUY or not self.quantity or self.gross_amount is None:
            return None
        gross = self.gross_amount * 100 if is_bond else self.gross_amount
        return div8(gross, self.quantity)


@dataclass(frozen=True, slots=True)
class Mapped:
    account_code: str | None
    entries: tuple[Entry, ...]
    bond_isins: frozenset[str]  # ISINs proven to be bonds by the file (D-102)
    possibly_truncated: bool
    first_date: date | None  # range of the file's operation dates (D-104)
    last_date: date | None

    def counts(self) -> Counter[Category]:
        return Counter(e.category for e in self.entries)


def map_directa(file: DirectaFile, *, move_cash: bool = True) -> Mapped:
    """Map every row of the file to exactly one entry. `move_cash=False` is the option
    "I did not record the deposits: do not move cash" (§7, D-49)."""
    rows = file.rows
    position = {row.line: index for index, row in enumerate(rows)}
    repeated = {p for p, n in Counter(r.protocol for r in rows if r.protocol).items() if n > 1}

    entries: list[Entry] = []
    orders: dict[str, list[DirectaRow]] = defaultdict(list)
    standalone: list[DirectaRow] = []
    for row in rows:
        (orders[row.order_ref] if row.order_ref else standalone).append(row)
    for ref, group in orders.items():
        entries.append(_order(ref, group, repeated))

    incomes: list[DirectaRow] = []
    withholdings: list[DirectaRow] = []
    for row in standalone:
        rejected = _standalone_rejection(row, repeated)
        if rejected is not None:
            entries.append(rejected)
            continue
        kind = VERIFIED[row.movement]
        if kind in _INCOME:
            incomes.append(row)
        elif kind in _WITHHOLDINGS:
            withholdings.append(row)
        else:
            entries.append(_cash_row(row, kind))
    entries.extend(_income(incomes, withholdings))

    entries = _with_sequences(entries, position)
    if not move_cash:
        entries = [_without_cash(e) for e in entries]
    # Chronological (§8): by the entry's last row in the file, i.e. its oldest row.
    entries.sort(key=lambda e: max(position[n] for n in e.lines), reverse=True)
    keys = Counter(e.key for e in entries)
    assert all(n == 1 for n in keys.values()), "entry keys must be unique"
    assert sum(len(e.lines) for e in entries) == len(rows), "every row in exactly one entry"

    days = [r.trade_date for r in rows if r.trade_date is not None]
    return Mapped(
        account_code=file.account_code,
        entries=tuple(entries),
        bond_isins=_bond_isins(rows),
        possibly_truncated=file.possibly_truncated,
        first_date=min(days, default=None),
        last_date=max(days, default=None),
    )


# --- Orders (§3 "Trades") -----------------------------------------------------------------


def _order(ref: str, rows: list[DirectaRow], repeated: set[str]) -> Entry:
    lines = tuple(r.line for r in rows)
    base = Entry(key=f"order:{ref}", category=Category.AMBIGUOUS, lines=lines)
    reasons = _order_rejections(rows, repeated)
    if reasons:
        return replace(base, reasons=tuple(reasons))

    def total(kind: Kind) -> Decimal:
        return sum((r.amount for r in rows if VERIFIED[r.movement] is kind), ZERO)

    main = [r for r in rows if VERIFIED[r.movement] is Kind.BUY]
    gross, fees, accrued = -total(Kind.BUY), -total(Kind.FEES), -total(Kind.ACCRUED)
    has = {VERIFIED[r.movement] for r in rows}
    taxes = -total(Kind.TAX_CREDIT) if Kind.TAX_CREDIT in has else None
    field_rules = [
        name
        for name, value in (
            ("gross_amount", gross),
            ("fees", fees),
            ("accrued_interest_paid", accrued),
        )
        if value < 0
    ]
    if field_rules:
        return replace(base, reasons=tuple(_reason("FIELD_RULE", field=f) for f in field_rules))

    unconfirmed = [
        _reason("UNCONFIRMED_MOVEMENT_TYPE", original_type=t, mapped_as=UNCONFIRMED[t])
        for t in (r.movement for r in rows)
        if t in UNCONFIRMED
    ]
    first = main[0]
    return replace(
        base,
        category=Category.TO_REVIEW if unconfirmed else Category.TO_IMPORT,
        reasons=tuple(dict.fromkeys(unconfirmed)),
        info=(_reason("MERGED_EXECUTIONS", count=len(main)),) if len(main) > 1 else (),
        type=T.BUY,
        trade_date=first.trade_date,
        value_date=first.value_date,
        isin=first.isin,
        ticker=first.ticker,
        description=first.description,
        quantity=sum((r.quantity for r in main), ZERO),
        gross_amount=gross,
        fees=fees,  # BUY fees exist for every asset type: 0 when there is no fee row
        accrued_interest_paid=accrued if Kind.ACCRUED in has else None,  # bonds only
        taxes=taxes,  # bonds only (D-42)
        total_amount=-sum((r.amount for r in rows), ZERO),  # = gross + fees + accrued + taxes
        external_ref=ref,
    )


def _order_rejections(rows: list[DirectaRow], repeated: set[str]) -> list[Reason]:
    unknown = [r for r in rows if r.movement not in VERIFIED]
    if unknown:
        return [_reason("ORDER_WITH_UNRECOGNIZED_ROW", original_type=r.movement) for r in unknown]
    reasons: list[Reason] = []
    if any(_foreign(r) for r in rows):
        reasons.append(_reason("FOREIGN_CURRENCY"))
    reasons += _cell_problems(rows)
    reasons += [
        _reason("DUPLICATE_PROTOCOL", protocol=r.protocol) for r in rows if r.protocol in repeated
    ]
    if reasons:
        return reasons
    kinds = [VERIFIED[r.movement] for r in rows]
    outside = [r for r, k in zip(rows, kinds, strict=True) if k not in _ORDER_KINDS]
    reasons += [_reason("ROW_TYPE_NOT_IN_ORDER", original_type=r.movement) for r in outside]
    if Kind.BUY not in kinds:
        reasons.append(_reason("ORDER_WITHOUT_MAIN_ROW"))
    if len({(r.trade_date, r.value_date) for r in rows}) > 1:
        reasons.append(_reason("ORDER_MIXED_DATES"))
    isins = {r.isin for r in rows}
    if None in isins:
        reasons.append(_reason("MISSING_ISIN"))
    elif len(isins) > 1:
        reasons.append(_reason("ORDER_MIXED_ISINS"))
    for row, kind in zip(rows, kinds, strict=True):
        if kind is Kind.BUY and row.quantity <= 0:
            reasons.append(_reason("FIELD_RULE", field="quantity"))
        elif kind is not Kind.BUY and row.quantity != 0:
            reasons.append(_reason("QUANTITY_ON_SIDE_ROW", original_type=row.movement))
    return reasons  # fmt: skip


# --- Rows without an order reference ----------------------------------------------------------


def _standalone_rejection(row: DirectaRow, repeated: set[str]) -> Entry | None:
    def reject(category: Category, *reasons: Reason) -> Entry:
        return Entry(key=f"line:{row.line}", category=category, lines=(row.line,), reasons=reasons)

    kind = VERIFIED.get(row.movement)
    if kind is None:
        return reject(
            Category.NOT_RECOGNIZED,
            _reason("MOVEMENT_TYPE_NOT_VERIFIED", original_type=row.movement),
        )
    if kind is Kind.BUY:  # D-49
        return reject(Category.NOT_RECOGNIZED, _reason("BUY_WITHOUT_ORDER_REF"))
    if kind in _ORDER_KINDS:
        return reject(
            Category.NOT_RECOGNIZED,
            _reason("SIDE_ROW_WITHOUT_ORDER_REF", original_type=row.movement),
        )
    if _foreign(row):
        return reject(Category.NOT_RECOGNIZED, _reason("FOREIGN_CURRENCY"))
    reasons = _cell_problems([row])
    if row.protocol in repeated:
        reasons.append(_reason("DUPLICATE_PROTOCOL", protocol=row.protocol))
    if row.protocol is None:
        reasons.append(_reason("MISSING_PROTOCOL"))
    if row.quantity is not None and row.quantity != 0:  # D-106
        reasons.append(_reason("QUANTITY_ON_SIDE_ROW", original_type=row.movement))
    if (kind in _INCOME or kind in _WITHHOLDINGS) and row.isin is None:
        reasons.append(_reason("MISSING_ISIN"))
    if reasons:
        return reject(Category.AMBIGUOUS, *reasons)
    return None


def _cash_row(row: DirectaRow, kind: Kind) -> Entry:
    """Deposit or account expense: one transaction per row (§3 "Other rows")."""
    base = Entry(key=f"protocol:{row.protocol}", category=Category.TO_IMPORT, lines=(row.line,))
    if kind is Kind.DEPOSIT:
        if row.amount <= 0:
            return replace(
                base,
                category=Category.AMBIGUOUS,
                reasons=(_reason("FIELD_RULE", field="total_amount"),),
            )
        return replace(
            base, type=T.DEPOSIT, trade_date=row.trade_date, value_date=row.value_date,
            total_amount=row.amount, external_ref=row.protocol,
        )  # fmt: skip
    if row.amount >= 0:
        return replace(
            base,
            category=Category.AMBIGUOUS,
            reasons=(_reason("FIELD_RULE", field="total_amount"),),
        )
    return replace(
        base, type=T.WITHDRAWAL, trade_date=row.trade_date, value_date=row.value_date,
        total_amount=-row.amount, external_ref=row.protocol, notes=row.movement,
        metadata=MappingProxyType({"original_type": row.movement}), account_expense=True,
        info=(_reason("ACCOUNT_EXPENSE", original_type=row.movement),),
    )  # fmt: skip


# --- Income and withholding (§3 "Income") -------------------------------------------------


def _income(incomes: list[DirectaRow], withholdings: list[DirectaRow]) -> list[Entry]:
    def matches(income: DirectaRow, tax: DirectaRow) -> bool:
        return (
            VERIFIED[tax.movement] is _INCOME[VERIFIED[income.movement]][1]
            and tax.trade_date == income.trade_date
            and tax.isin == income.isin
        )

    pairs: dict[int, tuple[DirectaRow, bool]] = {}  # income line -> (withholding, inferred)
    free = list(withholdings)
    for income in incomes:  # 1. consecutive protocols (withholding = income + 1)
        for tax in free:
            if matches(income, tax) and _next_protocol(income.protocol) == tax.protocol:
                pairs[income.line] = (tax, False)
                free.remove(tax)
                break
    rest = [i for i in incomes if i.line not in pairs]
    candidates = {i.line: [t for t in free if matches(i, t)] for i in rest}
    claimed = Counter(t.line for ts in candidates.values() for t in ts)
    ambiguous_lines: set[int] = set()
    for income in rest:  # 2. exactly one candidate on both sides: inferred pairing
        found = candidates[income.line]
        if len(found) == 1 and claimed[found[0].line] == 1:
            pairs[income.line] = (found[0], True)
            free.remove(found[0])
        elif found:
            ambiguous_lines.add(income.line)
            ambiguous_lines.update(t.line for t in found)

    entries: list[Entry] = []
    for income in incomes:
        if income.line in ambiguous_lines:
            entries.append(_ambiguous_row(income, "PAIRING_AMBIGUOUS"))
            continue
        tax, inferred = pairs.get(income.line, (None, False))
        entries.append(_income_entry(income, tax, inferred))
    for tax in free:
        code = "PAIRING_AMBIGUOUS" if tax.line in ambiguous_lines else "WITHHOLDING_WITHOUT_INCOME"
        entries.append(_ambiguous_row(tax, code))
    return entries


def _income_entry(income: DirectaRow, tax: DirectaRow | None, inferred: bool) -> Entry:
    tx_type = _INCOME[VERIFIED[income.movement]][0]
    lines = (income.line,) + ((tax.line,) if tax else ())
    base = Entry(key=f"protocol:{income.protocol}", category=Category.AMBIGUOUS, lines=lines)
    taxes = -tax.amount if tax else ZERO
    rules = [f for f, bad in (("gross_amount", income.amount <= 0), ("taxes", taxes < 0)) if bad]
    if rules:
        return replace(base, reasons=tuple(_reason("FIELD_RULE", field=f) for f in rules))
    info = []
    if tax is not None:
        info.append(_reason("WITHHOLDING_PAIRED", amount=taxes))
        if inferred:
            info.append(_reason("PAIRING_INFERRED"))
    return replace(
        base,
        category=Category.TO_IMPORT,
        info=tuple(info),
        type=tx_type,
        trade_date=income.trade_date,
        value_date=income.value_date,
        isin=income.isin,
        ticker=income.ticker,
        description=income.description,
        gross_amount=income.amount,
        taxes=taxes,
        total_amount=income.amount - taxes,
        external_ref=income.protocol,
    )


def _ambiguous_row(row: DirectaRow, code: str) -> Entry:
    return Entry(
        key=f"line:{row.line}",
        category=Category.AMBIGUOUS,
        lines=(row.line,),
        reasons=(_reason(code),),
    )


# --- Helpers ---------------------------------------------------------------------------------


def _foreign(row: DirectaRow) -> bool:
    """Non-EUR rows have never been observed: not recognized (§1)."""
    return row.currency != "EUR" or (row.foreign_amount is not None and row.foreign_amount != 0)


def _cell_problems(rows: Iterable[DirectaRow]) -> list[Reason]:
    return [
        _reason("CELL_PROBLEM", column=p.column, problem=p.code, line=r.line)
        for r in rows
        for p in r.problems
    ]


def _next_protocol(protocol: str | None) -> str | None:
    if protocol is None or not protocol.isdigit():
        return None
    return str(int(protocol) + 1)


def _bond_isins(rows: Iterable[DirectaRow]) -> frozenset[str]:
    return frozenset(
        r.isin
        for r in rows
        if r.isin and ((r.ticker or "").startswith("M.") or r.movement in BOND_ROWS)
    )


def _with_sequences(entries: list[Entry], position: Mapping[int, int]) -> list[Entry]:
    """Insertion sequence in reverse file order (oldest first); a merged transaction takes
    the sequence of its last row in the file (§8, D-49). Only selectable entries get one."""
    selectable = sorted(
        (e for e in entries if e.selectable), key=lambda e: max(position[n] for n in e.lines)
    )
    sequence = {e.key: n for n, e in enumerate(reversed(selectable), start=1)}
    return [replace(e, sequence=sequence.get(e.key)) for e in entries]


def _without_cash(entry: Entry) -> Entry:
    """The "do not move cash" option (§7): trades and income keep their figures but do not
    move cash; deposits and account expenses are skipped by default (selectable)."""
    if entry.type in (T.BUY, T.DIVIDEND, T.COUPON):
        return replace(entry, affects_cash=False)
    if entry.type in (T.DEPOSIT, T.WITHDRAWAL) and entry.category is Category.TO_IMPORT:
        return replace(entry, category=Category.SKIPPED_BY_CHOICE)
    return entry
