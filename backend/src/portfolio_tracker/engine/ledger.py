"""Replay of one account's ledger: positions, cost basis, realized gain, cash (domain-rules §1-§11).

Transactions are assumed field-valid (`validation`). The replay rejects a negative quantity
and reports warnings (negative cash, income on a never-held asset) without rejecting.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from portfolio_tracker.engine.decimals import div8, engine_context, mul_div8
from portfolio_tracker.engine.model import ZERO, Transaction, TransactionType
from portfolio_tracker.engine.ordering import replay_order

T = TransactionType


class NegativeQuantityError(ValueError):
    """The replay would take an asset below zero; names the first conflicting transaction."""

    def __init__(self, transaction_id: UUID, asset_id: UUID, quantity: Decimal) -> None:
        super().__init__(f"transaction {transaction_id} takes asset {asset_id} to {quantity}")
        self.transaction_id = transaction_id
        self.asset_id = asset_id
        self.quantity = quantity


class WarningCode(StrEnum):
    NEGATIVE_CASH = "NEGATIVE_CASH"
    INCOME_WITHOUT_HOLDING = "INCOME_WITHOUT_HOLDING"


@dataclass(frozen=True, slots=True)
class LedgerWarning:
    code: WarningCode
    date: date
    transaction_id: UUID | None


@dataclass(slots=True)
class Position:
    """Per-asset state of an account after the replay. Amounts in account currency."""

    asset_id: UUID
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    cost_removed: Decimal = ZERO  # lifetime, base of realized % (D-41)
    realized_gross: Decimal = ZERO
    ever_held: bool = False

    @property
    def pmc(self) -> Decimal | None:
        """Average cost per unit, display only: never fed back into calculations (D-40)."""
        return div8(self.cost_basis, self.quantity) if self.quantity else None


@dataclass(frozen=True, slots=True)
class Ledger:
    positions: dict[UUID, Position]
    cash: Decimal
    warnings: tuple[LedgerWarning, ...] = field(default=())


def replay(transactions: Iterable[Transaction], *, as_of: date | None = None) -> Ledger:
    """Replay in deterministic order (§4), optionally only up to and including `as_of`."""
    positions: dict[UUID, Position] = {}
    warnings: list[LedgerWarning] = []
    cash = ZERO
    cash_warned = False
    current_day: date | None = None

    with engine_context():
        for tx in replay_order(transactions):
            if as_of is not None and tx.trade_date > as_of:
                break
            if current_day is not None and tx.trade_date != current_day:
                cash_warned = _check_cash(cash, current_day, cash_warned, warnings)
            current_day = tx.trade_date

            if tx.asset_id is not None:
                pos = positions.setdefault(tx.asset_id, Position(tx.asset_id))
                _apply(tx, pos, warnings)
            cash += _cash_effect(tx)

        if current_day is not None:
            _check_cash(cash, current_day, cash_warned, warnings)

    return Ledger(positions, cash, tuple(warnings))


def _apply(tx: Transaction, pos: Position, warnings: list[LedgerWarning]) -> None:
    if tx.type is T.BUY:
        assert tx.quantity is not None and tx.gross_amount is not None
        pos.quantity += tx.quantity
        pos.cost_basis += tx.gross_amount + tx.fees
        pos.ever_held = True
    elif tx.type is T.SELL:
        assert tx.quantity is not None and tx.gross_amount is not None
        if tx.quantity > pos.quantity:
            raise NegativeQuantityError(tx.id, pos.asset_id, pos.quantity - tx.quantity)
        if tx.quantity == pos.quantity:
            removed = pos.cost_basis  # exact, so a closed position keeps no residue
        else:
            removed = mul_div8(pos.cost_basis, tx.quantity, pos.quantity)
        pos.quantity -= tx.quantity
        pos.cost_basis -= removed
        pos.cost_removed += removed
        pos.realized_gross += tx.gross_amount - tx.fees - removed
    elif tx.type is T.STOCK_SPLIT:
        assert tx.split_ratio is not None
        pos.quantity = mul_div8(pos.quantity, tx.split_ratio.new, tx.split_ratio.old)
    elif tx.type in (T.DIVIDEND, T.COUPON) and not pos.ever_held:
        warnings.append(LedgerWarning(WarningCode.INCOME_WITHOUT_HOLDING, tx.trade_date, tx.id))


def _cash_effect(tx: Transaction) -> Decimal:
    if not tx.affects_cash or tx.total_amount is None:
        return ZERO
    if tx.type in (T.BUY, T.WITHDRAWAL):
        return -tx.total_amount
    return tx.total_amount


def _check_cash(cash: Decimal, day: date, warned: bool, warnings: list[LedgerWarning]) -> bool:
    """End-of-day cash check; only the first negative date is reported."""
    if not warned and cash < 0:
        warnings.append(LedgerWarning(WarningCode.NEGATIVE_CASH, day, None))
        return True
    return warned
