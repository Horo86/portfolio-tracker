"""Group Directa rows into transactions (`docs/import/directa.md` §2-§3, §7, §8).

Pure: the result refers to assets by ISIN; the import service resolves them, finds the
duplicates already stored and validates the batch with the engine. Every row ends up in
exactly one candidate (`importers.base`), in one category:

- TO_IMPORT: verified and consistent, selected by default;
- TO_REVIEW: mapped, but containing a movement type whose meaning is not confirmed (D-99), or
  a sale whose capital gains tax row is linked by date only (D-158): not selected by
  default, selectable;
- SKIPPED_BY_CHOICE: a deposit or account expense skipped by the "do not move cash" option
  (D-49): not selected by default, selectable;
- AMBIGUOUS: inconsistent rows (D-108, D-105, D-106): never imported;
- NOT_RECOGNIZED: a movement type or shape that is not verified (D-20): never imported.

Reasons and notes are stable codes with parameters; the UI translates them.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

from portfolio_tracker.engine.decimals import exact
from portfolio_tracker.engine.model import AssetType, QuoteUnit, TransactionType
from portfolio_tracker.importers.base import (
    AssetRef,
    Candidate,
    Category,
    ParsedImport,
    Reason,
    without_cash,
)
from portfolio_tracker.importers.base import reason as _reason
from portfolio_tracker.importers.codes import LineCode, NoticeCode
from portfolio_tracker.importers.directa_reader import DirectaFile, DirectaRow

T = TransactionType
ZERO = Decimal(0)
SOURCE = "directa-xlsx"
LINKED_TAX = "linked_tax_protocol"  # a sale's metadata: the protocol of its linked tax row


class Kind(Enum):
    BUY = "BUY"  # main row of a purchase order
    SELL = "SELL"  # main row of a sale order (§2a, D-158)
    FEES = "FEES"
    ACCRUED = "ACCRUED"
    TAX_CREDIT = "TAX_CREDIT"
    COUPON = "COUPON"
    COUPON_TAX = "COUPON_TAX"
    DIVIDEND = "DIVIDEND"
    DIVIDEND_TAX = "DIVIDEND_TAX"
    DEPOSIT = "DEPOSIT"
    EXPENSE = "EXPENSE"
    SALE_TAX = "SALE_TAX"  # withholding inside a sale order ("Rit. etf")
    GAIN_TAX = "GAIN_TAX"  # capital gains tax on its own row, linked to a sale by date


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
        "Vendita": Kind.SELL,
        "Rit. etf": Kind.SALE_TAX,
        "Ritenuta su plusvalenza": Kind.GAIN_TAX,
    }
)
# Mapped as documented but not confirmed by the broker: the transaction is "to review" (D-99).
UNCONFIRMED: Mapping[str, str] = MappingProxyType({"Rit.credito disaggio": "TAX_CREDIT"})
# Rows that prove an ISIN is a bond (D-102), besides a ticker starting with "M." (§1).
BOND_ROWS = frozenset({"Cedola obb.", "Ratei pass.obb.", "Rit.ratei pass.obb."})

_PROTOCOL = re.compile(r"[0-9]{1,18}")
_MAIN_KINDS = frozenset({Kind.BUY, Kind.SELL})
# The side rows verified with each kind of order (§2a, §3).
_SIDES = {
    Kind.BUY: frozenset({Kind.FEES, Kind.ACCRUED, Kind.TAX_CREDIT}),
    Kind.SELL: frozenset({Kind.FEES, Kind.SALE_TAX}),
}
_ORDER_KINDS = _MAIN_KINDS | _SIDES[Kind.BUY] | _SIDES[Kind.SELL]
_INCOME = {Kind.COUPON: (T.COUPON, Kind.COUPON_TAX), Kind.DIVIDEND: (T.DIVIDEND, Kind.DIVIDEND_TAX)}
_WITHHOLDINGS = frozenset({Kind.COUPON_TAX, Kind.DIVIDEND_TAX})


@exact  # sums with the engine precision, never Python's default 28 digits (D-98)
def map_directa(file: DirectaFile, *, move_cash: bool = True) -> ParsedImport:
    """Map every row of the file to exactly one candidate. `move_cash=False` is the option
    "I did not record the deposits: do not move cash" (§7, D-49)."""
    rows = file.rows
    position = {row.line: index for index, row in enumerate(rows)}
    repeated = {p for p, n in Counter(r.protocol for r in rows if r.protocol).items() if n > 1}

    bonds = _bond_isins(rows)
    entries: list[Candidate] = []
    orders: dict[str, list[DirectaRow]] = defaultdict(list)
    standalone: list[DirectaRow] = []
    for row in rows:
        (orders[row.order_ref] if row.order_ref else standalone).append(row)
    sales: dict[str, tuple[list[DirectaRow], Candidate]] = {}
    for ref, group in orders.items():
        entry = _order(ref, group, repeated, bonds)
        if any(r.movement in VERIFIED and VERIFIED[r.movement] is Kind.SELL for r in group):
            sales[ref] = (group, entry)
        else:
            entries.append(entry)

    incomes: list[DirectaRow] = []
    withholdings: list[DirectaRow] = []
    gain_taxes: list[DirectaRow] = []
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
        elif kind is Kind.GAIN_TAX:
            gain_taxes.append(row)
        else:
            entries.append(_cash_row(row, kind))
    # Withholding rows that are not in the pairing pool (rejected, or inside an order): an
    # income they may belong to must not be imported with taxes 0 (D-108).
    pool = {r.line for r in withholdings}
    lost = [r for r in rows if VERIFIED.get(r.movement) in _WITHHOLDINGS and r.line not in pool]
    entries.extend(_income(incomes, withholdings, lost))
    # The same for capital gains tax rows: a sale they may belong to is not imported with
    # taxes 0 (R-6 of the M5 review, D-160).
    taxed = {r.line for r in gain_taxes}
    lost_taxes = [
        r for r in rows if VERIFIED.get(r.movement) is Kind.GAIN_TAX and r.line not in taxed
    ]
    sales, lost_notices = _without_lost_taxes(sales, lost_taxes)
    linked, tax_notices = _link_gain_taxes(sales, gain_taxes)
    tax_notices += lost_notices
    entries.extend(linked)

    entries = _with_sequences(entries, position)
    if not move_cash:
        entries = [without_cash(e) for e in entries]
    # Chronological (§8): by the entry's last row in the file, i.e. its oldest row.
    entries.sort(key=lambda e: max(position[n] for n in e.lines), reverse=True)
    # Invariants of this module (a bug if broken): checked even under `python -O`.
    if any(n > 1 for n in Counter(e.key for e in entries).values()):
        raise RuntimeError("Directa mapping: candidate keys are not unique")
    if sorted(n for e in entries for n in e.lines) != sorted(r.line for r in rows):
        raise RuntimeError("Directa mapping: a row is not in exactly one candidate")

    entries = [_with_proposal(e, bonds) for e in entries]
    days = [r.trade_date for r in rows if r.trade_date is not None]
    notices = (
        (_reason(NoticeCode.POSSIBLY_TRUNCATED, rows=len(rows)),) if file.possibly_truncated else ()
    ) + tax_notices
    return ParsedImport(
        source=SOURCE,
        candidates=tuple(entries),
        account_code=file.account_code,
        notices=notices,
        first_date=min(days, default=None),
        last_date=max(days, default=None),
    )


# --- Orders (§3 "Trades") -----------------------------------------------------------------


def _order(
    ref: str, rows: list[DirectaRow], repeated: set[str], bonds: frozenset[str]
) -> Candidate:
    lines = tuple(r.line for r in rows)
    base = Candidate(key=f"order:{ref}", category=Category.AMBIGUOUS, lines=lines)
    reasons = _order_rejections(rows, repeated)
    if reasons:
        return replace(base, reasons=tuple(reasons))

    def total(kind: Kind) -> Decimal:
        return sum((r.amount for r in rows if VERIFIED[r.movement] is kind), ZERO)

    if any(VERIFIED[r.movement] is Kind.SELL for r in rows):
        return _sale(ref, rows, base, bonds, total)
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
        return replace(
            base, reasons=tuple(_reason(LineCode.FIELD_RULE, field=f) for f in field_rules)
        )

    unconfirmed = [
        _reason(LineCode.UNCONFIRMED_MOVEMENT_TYPE, original_type=t, mapped_as=UNCONFIRMED[t])
        for t in (r.movement for r in rows)
        if t in UNCONFIRMED
    ]
    first = main[0]
    return replace(
        base,
        category=Category.TO_REVIEW if unconfirmed else Category.TO_IMPORT,
        reasons=tuple(dict.fromkeys(unconfirmed)),
        info=(_reason(LineCode.MERGED_EXECUTIONS, count=len(main)),) if len(main) > 1 else (),
        type=T.BUY,
        trade_date=first.trade_date,
        value_date=first.value_date,
        asset=AssetRef(isin=first.isin, ticker=first.ticker, name=first.description),
        quantity=sum((r.quantity for r in main), ZERO),
        gross_amount=gross,
        fees=fees,  # BUY fees exist for every asset type: 0 when there is no fee row
        accrued_interest_paid=accrued if Kind.ACCRUED in has else None,  # bonds only
        taxes=taxes,  # bonds only (D-42)
        total_amount=-sum((r.amount for r in rows), ZERO),  # = gross + fees + accrued + taxes
        external_ref=ref,
    )


def _sale(
    ref: str, rows: list[DirectaRow], base: Candidate, bonds: frozenset[str], total
) -> Candidate:
    """A sale order (§2a, D-158): every execution, fee and withholding of the order summed.
    The signs of every row were checked (`_order_rejections`), so the amounts meet the SELL
    rule `total = gross - fees - taxes` (domain-rules §2) by construction: the total is the
    sum of the order's amounts, the cash it brought in."""
    main = [r for r in rows if VERIFIED[r.movement] is Kind.SELL]
    first = main[0]
    if first.isin in bonds:  # the rows of a bond sale (accrued interest) were never seen
        return replace(base, reasons=(_reason(LineCode.BOND_SALE_NOT_VERIFIED),))
    return replace(
        base,
        category=Category.TO_IMPORT,
        info=(_reason(LineCode.MERGED_EXECUTIONS, count=len(main)),) if len(main) > 1 else (),
        type=T.SELL,
        trade_date=first.trade_date,
        value_date=first.value_date,
        asset=AssetRef(isin=first.isin, ticker=first.ticker, name=first.description),
        quantity=sum((r.quantity for r in main), ZERO),
        gross_amount=total(Kind.SELL),
        fees=-total(Kind.FEES),
        taxes=-total(Kind.SALE_TAX),  # recorded as given, never recomputed (hard rule 8)
        total_amount=sum((r.amount for r in rows), ZERO),
        external_ref=ref,
    )


def _order_rejections(rows: list[DirectaRow], repeated: set[str]) -> list[Reason]:
    unknown = [r for r in rows if r.movement not in VERIFIED]
    if unknown:
        return [
            _reason(LineCode.ORDER_WITH_UNRECOGNIZED_ROW, original_type=r.movement) for r in unknown
        ]
    reasons: list[Reason] = []
    if any(_foreign(r) for r in rows):
        reasons.append(_reason(LineCode.FOREIGN_CURRENCY))
    reasons += _cell_problems(rows)
    reasons += [
        _reason(LineCode.DUPLICATE_PROTOCOL, protocol=r.protocol)
        for r in rows
        if r.protocol in repeated
    ]
    if reasons:
        return reasons
    kinds = [VERIFIED[r.movement] for r in rows]
    outside = [r for r, k in zip(rows, kinds, strict=True) if k not in _ORDER_KINDS]
    reasons += [_reason(LineCode.ROW_TYPE_NOT_IN_ORDER, original_type=r.movement) for r in outside]
    mains = _MAIN_KINDS.intersection(kinds)
    if not mains:
        reasons.append(_reason(LineCode.ORDER_WITHOUT_MAIN_ROW))
    elif len(mains) > 1:
        reasons.append(_reason(LineCode.ORDER_MIXED_SIDES))
    else:
        (main,) = mains
        other = LineCode.ROW_NOT_VERIFIED_IN_SALE if main is Kind.SELL else (
            LineCode.ROW_NOT_VERIFIED_IN_PURCHASE)  # fmt: skip
        reasons += [
            _reason(other, original_type=r.movement)
            for r, k in zip(rows, kinds, strict=True)
            if k in _ORDER_KINDS and k not in _MAIN_KINDS and k not in _SIDES[main]
        ]
    if len({(r.trade_date, r.value_date) for r in rows}) > 1:
        reasons.append(_reason(LineCode.ORDER_MIXED_DATES))
    isins = {r.isin for r in rows}
    if None in isins:
        reasons.append(_reason(LineCode.MISSING_ISIN))
    elif len(isins) > 1:
        reasons.append(_reason(LineCode.ORDER_MIXED_ISINS))
    selling = mains == {Kind.SELL}
    for row, kind in zip(rows, kinds, strict=True):
        if kind is Kind.BUY and row.amount >= 0:  # every execution is cash out (D-108)
            reasons.append(_reason(LineCode.FIELD_RULE, field="gross_amount", line=row.line))
        if kind is Kind.SELL and row.amount <= 0:  # every execution is cash in (D-158)
            reasons.append(_reason(LineCode.FIELD_RULE, field="gross_amount", line=row.line))
        if kind is Kind.BUY and row.quantity <= 0:
            reasons.append(_reason(LineCode.FIELD_RULE, field="quantity"))
        elif kind is Kind.SELL and row.quantity <= 0:
            reasons.append(_reason(LineCode.FIELD_RULE, field="quantity", line=row.line))
        elif kind not in _MAIN_KINDS and row.quantity != 0:
            reasons.append(_reason(LineCode.QUANTITY_ON_SIDE_ROW, original_type=row.movement))
        if selling:
            reasons += _sale_side_sign(row, kind)
    return reasons  # fmt: skip


def _sale_side_sign(row: DirectaRow, kind: Kind) -> list[Reason]:
    """D-158: fee and tax rows of a sale are negative; a positive tax row would be a credit,
    never observed (§2a)."""
    if kind is Kind.FEES and row.amount >= 0:
        return [_reason(LineCode.FIELD_RULE, field="fees", line=row.line)]
    if kind is Kind.SALE_TAX and row.amount > 0:
        return [_reason(LineCode.TAX_CREDIT_NOT_VERIFIED, original_type=row.movement,
                        line=row.line)]  # fmt: skip
    if kind is Kind.SALE_TAX and row.amount == 0:
        return [_reason(LineCode.FIELD_RULE, field="taxes", line=row.line)]
    return []


# --- Rows without an order reference ----------------------------------------------------------


def _standalone_rejection(row: DirectaRow, repeated: set[str]) -> Candidate | None:
    def reject(category: Category, *reasons: Reason) -> Candidate:
        return Candidate(
            key=f"line:{row.line}", category=category, lines=(row.line,), reasons=reasons
        )

    kind = VERIFIED.get(row.movement)
    if kind is None:
        return reject(
            Category.NOT_RECOGNIZED,
            _reason(LineCode.MOVEMENT_TYPE_NOT_VERIFIED, original_type=row.movement),
        )
    if kind is Kind.BUY:  # D-49
        return reject(Category.NOT_RECOGNIZED, _reason(LineCode.BUY_WITHOUT_ORDER_REF))
    if kind in _ORDER_KINDS:
        return reject(
            Category.NOT_RECOGNIZED,
            _reason(LineCode.SIDE_ROW_WITHOUT_ORDER_REF, original_type=row.movement),
        )
    if _foreign(row):
        return reject(Category.NOT_RECOGNIZED, _reason(LineCode.FOREIGN_CURRENCY))
    reasons = _cell_problems([row])
    if row.protocol in repeated:
        reasons.append(_reason(LineCode.DUPLICATE_PROTOCOL, protocol=row.protocol))
    if row.protocol is None:
        reasons.append(_reason(LineCode.MISSING_PROTOCOL))
    if row.quantity is not None and row.quantity != 0:  # D-106
        reasons.append(_reason(LineCode.QUANTITY_ON_SIDE_ROW, original_type=row.movement))
    if (kind in _INCOME or kind in _WITHHOLDINGS) and row.isin is None:
        reasons.append(_reason(LineCode.MISSING_ISIN))
    if kind is Kind.GAIN_TAX and not reasons:
        if row.amount > 0:  # a credit: never observed (D-158)
            reasons.append(_reason(LineCode.TAX_CREDIT_NOT_VERIFIED, original_type=row.movement,
                                   line=row.line))  # fmt: skip
        elif row.amount == 0:
            reasons.append(_reason(LineCode.FIELD_RULE, field="taxes", line=row.line))
    if reasons:
        return reject(Category.AMBIGUOUS, *reasons)
    return None


def _cash_row(row: DirectaRow, kind: Kind) -> Candidate:
    """Deposit or account expense: one transaction per row (§3 "Other rows")."""
    base = Candidate(key=f"protocol:{row.protocol}", category=Category.TO_IMPORT, lines=(row.line,))
    if kind is Kind.DEPOSIT:
        if row.amount <= 0:
            return replace(
                base,
                category=Category.AMBIGUOUS,
                reasons=(_reason(LineCode.FIELD_RULE, field="total_amount"),),
            )
        return replace(
            base, type=T.DEPOSIT, trade_date=row.trade_date, value_date=row.value_date,
            total_amount=row.amount, external_ref=row.protocol,
        )  # fmt: skip
    if row.amount >= 0:
        return replace(
            base,
            category=Category.AMBIGUOUS,
            reasons=(_reason(LineCode.FIELD_RULE, field="total_amount"),),
        )
    return replace(
        base, type=T.WITHDRAWAL, trade_date=row.trade_date, value_date=row.value_date,
        total_amount=-row.amount, external_ref=row.protocol, notes=row.movement,
        metadata=MappingProxyType({"original_type": row.movement}), account_expense=True,
        info=(_reason(LineCode.ACCOUNT_EXPENSE, original_type=row.movement),),
    )  # fmt: skip


# --- Capital gains tax on its own row (§3 "Sales", D-158) -----------------------------------


def _settles(rows: list[DirectaRow]) -> set:
    """The value dates of a sale order without a "Rit. etf" row (one a separate tax row can
    belong to, rule 2); empty for any other order."""
    if any(VERIFIED.get(r.movement) is Kind.SALE_TAX for r in rows):
        return set()
    return {r.value_date for r in rows if VERIFIED.get(r.movement) is Kind.SELL} - {None}


def _without_lost_taxes(
    sales: Mapping[str, tuple[list[DirectaRow], Candidate]], lost: list[DirectaRow]
) -> tuple[dict[str, tuple[list[DirectaRow], Candidate]], tuple[Reason, ...]]:
    """Capital gains tax rows rejected by their own checks (another currency, a missing
    protocol, a cell that cannot be read...): every importable sale settling on one of the
    row's dates, or on any date when none can be read, becomes ambiguous (R-6, D-160). Each
    such row with a date and an amount gets the notice of a tax to add by hand."""
    out = dict(sales)
    notices: list[Reason] = []
    for tax in lost:
        days = {tax.trade_date, tax.value_date} - {None}
        for ref, (rows, sale) in sales.items():
            if sale.category is not Category.TO_IMPORT:
                continue
            settles = _settles(rows)
            if settles and (not days or days & settles):
                sale = out[ref][1]
                reasons = (*sale.reasons, _reason(LineCode.SALE_TAX_ROW_REJECTED, line=tax.line))
                out[ref] = (rows, replace(sale, category=Category.AMBIGUOUS, reasons=reasons))
        day = tax.trade_date or tax.value_date
        if day is not None and tax.amount is not None:
            notices.append(
                _reason(NoticeCode.SALE_TAX_TO_ADD_BY_HAND, date=day, amount=ZERO - tax.amount,
                        line=tax.line)
            )  # fmt: skip
    return out, tuple(notices)


def _link_gain_taxes(
    sales: Mapping[str, tuple[list[DirectaRow], Candidate]], taxes: list[DirectaRow]
) -> tuple[list[Candidate], tuple[Reason, ...]]:
    """Link each "Ritenuta su plusvalenza" row to the only sale ORDER of the file that settles
    on its date (its operation and value date are both the sale's value date) and has no
    "Rit. etf" row. A linked sale is "to review" (inferred, D-158 rule 2). A tax row with no
    such sale, with several, sharing its sale with another tax row, or whose sale is not
    importable is ambiguous, with a notice: it must be added by hand (rule 3)."""
    by_date: dict[object, list[str]] = defaultdict(list)
    for ref, (rows, _) in sales.items():
        for day in _settles(rows):
            by_date[day].append(ref)
    matches: dict[int, list[str]] = {}
    for tax in taxes:
        same = tax.trade_date == tax.value_date
        matches[tax.line] = by_date.get(tax.trade_date, []) if same else []
    claims = Counter(found[0] for found in matches.values() if len(found) == 1)

    out: list[Candidate] = []
    notices: list[Reason] = []
    linked: dict[str, DirectaRow] = {}
    for tax in taxes:
        found = matches[tax.line]
        problem: Reason | None = None
        if not found:
            problem = _reason(LineCode.SALE_TAX_NO_SALE)
        elif len(found) > 1:
            problem = _reason(LineCode.SALE_TAX_SEVERAL_SALES, count=len(found))
        elif claims[found[0]] > 1:
            problem = _reason(LineCode.SALE_TAX_SHARED)
        elif sales[found[0]][1].category is not Category.TO_IMPORT:
            problem = _reason(LineCode.SALE_TAX_SALE_REJECTED, external_ref=found[0])
        if problem is None:
            linked[found[0]] = tax
            continue
        out.append(
            Candidate(key=f"line:{tax.line}", category=Category.AMBIGUOUS, lines=(tax.line,),
                      reasons=(problem,))
        )  # fmt: skip
        notices.append(
            _reason(NoticeCode.SALE_TAX_TO_ADD_BY_HAND, date=tax.trade_date, amount=-tax.amount,
                    line=tax.line)
        )  # fmt: skip
    for ref, (_, sale) in sales.items():
        tax = linked.get(ref)
        if tax is not None:
            sale = replace(
                sale,
                category=Category.TO_REVIEW,
                lines=tuple(sorted((*sale.lines, tax.line))),
                reasons=(_reason(LineCode.SALE_TAX_LINKED_BY_DATE, amount=-tax.amount,
                                 date=tax.trade_date, line=tax.line),),
                taxes=sale.taxes - tax.amount,
                total_amount=sale.total_amount + tax.amount,
                metadata=MappingProxyType({LINKED_TAX: tax.protocol}),
            )  # fmt: skip
        out.append(sale)
    return out, tuple(notices)


# --- Income and withholding (§3 "Income") -------------------------------------------------


def _income(
    incomes: list[DirectaRow], withholdings: list[DirectaRow], lost: list[DirectaRow]
) -> list[Candidate]:
    """Pair each income with its withholding. Linear: rows are indexed by protocol and by
    (withholding kind, date, ISIN) (final review of M3, F4: a crafted file of 10,000 rows
    took 33 s with a scan per income)."""

    def wanted(income: DirectaRow) -> tuple[Kind, object, object]:
        """The withholding an income matches: its kind, the same date and ISIN."""
        return (_INCOME[VERIFIED[income.movement]][1], income.trade_date, income.isin)

    def key(tax: DirectaRow) -> tuple[Kind, object, object]:
        return (VERIFIED[tax.movement], tax.trade_date, tax.isin)

    pairs: dict[int, tuple[DirectaRow, bool]] = {}  # income line -> (withholding, inferred)
    used: set[int] = set()
    by_protocol = {t.protocol: t for t in withholdings}  # unique: repeats were rejected
    for income in incomes:  # 1. consecutive protocols (withholding = income + 1)
        tax = by_protocol.get(_next_protocol(income.protocol))
        if tax is not None and tax.line not in used and key(tax) == wanted(income):
            pairs[income.line] = (tax, False)
            used.add(tax.line)

    # 2. Inferred pairing: an income and a withholding that are each other's only match.
    # Every income of one (kind, date, ISIN) group has the same matches, so the rule is per
    # group: exactly one income and one withholding pair up; otherwise, when the group has
    # withholdings, all its rows are ambiguous.
    free_by_key: dict[tuple, list[DirectaRow]] = defaultdict(list)
    for tax in withholdings:
        if tax.line not in used:
            free_by_key[key(tax)].append(tax)
    rest_by_key: dict[tuple, list[DirectaRow]] = defaultdict(list)
    for income in incomes:
        if income.line not in pairs:
            rest_by_key[wanted(income)].append(income)
    ambiguous_lines: set[int] = set()
    for group, group_incomes in rest_by_key.items():
        found = free_by_key.get(group, [])
        if len(found) == 1 and len(group_incomes) == 1:
            pairs[group_incomes[0].line] = (found[0], True)
            used.add(found[0].line)
        elif found:
            ambiguous_lines.update(i.line for i in group_incomes)
            ambiguous_lines.update(t.line for t in found)

    # A rejected withholding row could be an income's: same kind, and the same date and ISIN
    # as far as they could be read (an unreadable one matches anything). First in file order.
    first_lost: dict[tuple, int] = {}
    for index, tax in enumerate(lost):
        first_lost.setdefault(key(tax), index)

    def suspect(income: DirectaRow) -> DirectaRow | None:
        kind, day, isin = wanted(income)
        hits = [first_lost.get((kind, d, i)) for d in (day, None) for i in (isin, None)]
        found = [h for h in hits if h is not None]
        return lost[min(found)] if found else None

    entries: list[Candidate] = []
    for income in incomes:
        if income.line in ambiguous_lines:
            entries.append(_ambiguous_row(income, LineCode.PAIRING_AMBIGUOUS))
            continue
        tax, inferred = pairs.get(income.line, (None, False))
        lost_row = suspect(income)
        if lost_row is not None and (tax is None or inferred):  # a consecutive protocol wins
            entries.append(_ambiguous_row(income, LineCode.PAIRED_ROW_REJECTED, line=lost_row.line))
            if tax is not None:
                entries.append(
                    _ambiguous_row(tax, LineCode.PAIRED_ROW_REJECTED, line=lost_row.line)
                )
            continue
        entries.append(_income_entry(income, tax, inferred))
    for tax in withholdings:
        if tax.line in used:
            continue
        code = (
            LineCode.PAIRING_AMBIGUOUS
            if tax.line in ambiguous_lines
            else LineCode.WITHHOLDING_WITHOUT_INCOME
        )
        entries.append(_ambiguous_row(tax, code))
    return entries


def _income_entry(income: DirectaRow, tax: DirectaRow | None, inferred: bool) -> Candidate:
    tx_type = _INCOME[VERIFIED[income.movement]][0]
    lines = (income.line,) + ((tax.line,) if tax else ())
    base = Candidate(key=f"protocol:{income.protocol}", category=Category.AMBIGUOUS, lines=lines)
    taxes = -tax.amount if tax else ZERO
    rules = [f for f, bad in (("gross_amount", income.amount <= 0), ("taxes", taxes < 0)) if bad]
    if rules:
        return replace(base, reasons=tuple(_reason(LineCode.FIELD_RULE, field=f) for f in rules))
    info = []
    if tax is not None:
        info.append(_reason(LineCode.WITHHOLDING_PAIRED, amount=taxes))
        if inferred:
            info.append(_reason(LineCode.PAIRING_INFERRED))
    return replace(
        base,
        category=Category.TO_IMPORT,
        info=tuple(info),
        type=tx_type,
        trade_date=income.trade_date,
        value_date=income.value_date,
        asset=AssetRef(isin=income.isin, ticker=income.ticker, name=income.description),
        gross_amount=income.amount,
        taxes=taxes,
        total_amount=income.amount - taxes,
        external_ref=income.protocol,
    )


def _ambiguous_row(row: DirectaRow, code: str, **params: object) -> Candidate:
    return Candidate(
        key=f"line:{row.line}",
        category=Category.AMBIGUOUS,
        lines=(row.line,),
        reasons=(_reason(code, **params),),
    )


# --- Helpers ---------------------------------------------------------------------------------


def _foreign(row: DirectaRow) -> bool:
    """Non-EUR rows have never been observed: not recognized (§1)."""
    return row.currency != "EUR" or (row.foreign_amount is not None and row.foreign_amount != 0)


def _cell_problems(rows: Iterable[DirectaRow]) -> list[Reason]:
    return [
        _reason(LineCode.CELL_PROBLEM, column=p.column, problem=p.code, line=r.line)
        for r in rows
        for p in r.problems
    ]


def _next_protocol(protocol: str | None) -> str | None:
    # ASCII digits only: str.isdigit() also accepts e.g. "¹²", which int() refuses.
    if protocol is None or not _PROTOCOL.fullmatch(protocol):
        return None
    return str(int(protocol) + 1)


def _bond_isins(rows: Iterable[DirectaRow]) -> frozenset[str]:
    return frozenset(
        r.isin
        for r in rows
        if r.isin and ((r.ticker or "").startswith("M.") or r.movement in BOND_ROWS)
    )


def _with_sequences(entries: list[Candidate], position: Mapping[int, int]) -> list[Candidate]:
    """Insertion sequence in reverse file order (oldest first); a merged transaction takes
    the sequence of its last row in the file (§8, D-49). Only selectable entries get one."""
    selectable = sorted(
        (e for e in entries if e.selectable), key=lambda e: max(position[n] for n in e.lines)
    )
    sequence = {e.key: n for n, e in enumerate(reversed(selectable), start=1)}
    return [replace(e, sequence=sequence.get(e.key)) for e in entries]


def _with_proposal(candidate: Candidate, bonds: frozenset[str]) -> Candidate:
    """What the preview proposes for a new asset (D-102): every verified row is in EUR;
    BOND only on evidence in the file, otherwise no type (the user chooses)."""
    asset = candidate.asset
    if asset is None:
        return candidate
    if asset.isin in bonds:
        asset = replace(asset, asset_type=AssetType.BOND, quote_unit=QuoteUnit.PERCENT_OF_NOMINAL)
    return replace(candidate, asset=replace(asset, quote_currency="EUR"))
