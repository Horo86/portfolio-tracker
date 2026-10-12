"""Per-type field rules of a single transaction (domain-rules §2).

Issues carry a field name and a stable code; user-facing texts live in the UI translations.
Ledger-level rules (no negative quantity, cash warnings) are in `ledger`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from portfolio_tracker.engine.model import Asset, Transaction, TransactionType

T = TransactionType

TRADES = frozenset({T.BUY, T.SELL})
INCOME = frozenset({T.DIVIDEND, T.COUPON})
CASH_MOVEMENTS = frozenset({T.DEPOSIT, T.WITHDRAWAL})
WITH_ASSET = TRADES | INCOME | {T.STOCK_SPLIT}
WITH_CASH_FLAG = TRADES | INCOME

# Optional decimal fields that each type may carry with a non-zero value.
_ALLOWED_EXTRAS: dict[TransactionType, frozenset[str]] = {
    T.BUY: frozenset(
        {"quantity", "unit_price", "gross_amount", "fees", "accrued_interest_paid", "taxes"}
    ),
    T.SELL: frozenset(
        {"quantity", "unit_price", "gross_amount", "fees", "accrued_interest_received", "taxes"}
    ),
    T.DIVIDEND: frozenset({"quantity", "gross_amount", "taxes"}),
    T.COUPON: frozenset({"gross_amount", "taxes"}),
    T.DEPOSIT: frozenset(),
    T.WITHDRAWAL: frozenset(),
    T.STOCK_SPLIT: frozenset(),
}
_OPTIONAL_FIELDS = (
    "quantity",
    "unit_price",
    "gross_amount",
    "fees",
    "accrued_interest_paid",
    "accrued_interest_received",
    "taxes",
)


@dataclass(frozen=True, slots=True)
class FieldIssue:
    field: str
    code: str


Add = Callable[[str, str], None]


def validate_transaction(tx: Transaction, asset: Asset | None) -> list[FieldIssue]:
    """Return the field issues of `tx` (empty = valid); `asset` is the asset it references."""
    issues: list[FieldIssue] = []

    def add(field: str, code: str) -> None:
        issues.append(FieldIssue(field, code))

    _check_asset(tx, asset, add)
    _check_allowed_fields(tx, add)

    if tx.type not in WITH_CASH_FLAG and not tx.affects_cash:
        add("affects_cash", "not_allowed")

    if tx.type is T.STOCK_SPLIT:
        if tx.total_amount is not None:
            add("total_amount", "not_allowed")
        if tx.split_ratio is None:
            add("split_ratio", "required")
        elif tx.split_ratio.new <= 0 or tx.split_ratio.old <= 0:
            add("split_ratio", "must_be_positive")
        return issues
    if tx.split_ratio is not None:
        add("split_ratio", "not_allowed")

    if tx.total_amount is None:
        add("total_amount", "required")

    if tx.type in CASH_MOVEMENTS:
        if tx.total_amount is not None and tx.total_amount <= 0:
            add("total_amount", "must_be_positive")
        return issues

    if tx.type in TRADES:
        _positive(tx, "quantity", add)
        _non_negative(tx, "gross_amount", add, required=True)
        _non_negative(tx, "fees", add)
    else:
        _positive(tx, "gross_amount", add)
        _non_negative(tx, "taxes", add)
    _non_negative(tx, "accrued_interest_paid", add)
    _non_negative(tx, "accrued_interest_received", add)

    if not issues and tx.total_amount != _expected_total(tx):
        add("total_amount", "invariant")
    return issues


def _expected_total(tx: Transaction) -> Decimal:
    gross = tx.gross_amount or Decimal(0)
    if tx.type is T.BUY:
        return gross + tx.fees + tx.accrued_interest_paid + tx.taxes
    if tx.type is T.SELL:
        return gross + tx.accrued_interest_received - tx.fees - tx.taxes
    return gross - tx.taxes


def _check_asset(tx: Transaction, asset: Asset | None, add: Add) -> None:
    if tx.type not in WITH_ASSET:
        if tx.asset_id is not None:
            add("asset_id", "not_allowed")
        return
    if tx.asset_id is None:
        add("asset_id", "required")
        return
    if asset is None or asset.id != tx.asset_id:
        add("asset_id", "mismatch")
        return
    if tx.type is T.COUPON and not asset.is_bond:
        add("asset_id", "must_be_bond")
    if not asset.is_bond:
        if tx.accrued_interest_paid != 0 and tx.type is T.BUY:
            add("accrued_interest_paid", "bond_only")
        if tx.accrued_interest_received != 0 and tx.type is T.SELL:
            add("accrued_interest_received", "bond_only")
        if tx.taxes != 0 and tx.type is T.BUY:
            add("taxes", "bond_only")


def _check_allowed_fields(tx: Transaction, add: Add) -> None:
    allowed = _ALLOWED_EXTRAS[tx.type]
    for name in _OPTIONAL_FIELDS:
        value = getattr(tx, name)
        if name not in allowed and value is not None and value != 0:
            add(name, "not_allowed")


def _positive(tx: Transaction, name: str, add: Add) -> None:
    value = getattr(tx, name)
    if value is None:
        add(name, "required")
    elif value <= 0:
        add(name, "must_be_positive")


def _non_negative(tx: Transaction, name: str, add: Add, *, required: bool = False) -> None:
    value = getattr(tx, name)
    if value is None:
        if required:
            add(name, "required")
    elif value < 0:
        add(name, "must_not_be_negative")
