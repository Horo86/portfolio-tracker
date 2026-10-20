"""Replay of one account's ledger: positions, cost basis, realized gain, income, taxes, cash.

Rules: domain-rules §1-§11.

Transactions are assumed field-valid (`validation`). The replay rejects a negative quantity
or a split that rounds a held quantity to zero, and reports warnings (negative cash, income
on a never-held asset) without rejecting.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from portfolio_tracker.engine.decimals import (
    div8,
    engine_context,
    exact,
    mul_div8,
    or_zero,
    ratio8,
)
from portfolio_tracker.engine.model import ZERO, Transaction, TransactionType
from portfolio_tracker.engine.ordering import replay_order

T = TransactionType


class ConflictCode(StrEnum):
    NEGATIVE_QUANTITY = "NEGATIVE_QUANTITY"  # §4, D-06
    SPLIT_TO_ZERO = "SPLIT_TO_ZERO"  # D-69


class QuantityConflictError(ValueError):
    """The replay would leave an invalid quantity; names the first conflicting transaction."""

    code: ConflictCode

    def __init__(
        self, transaction_id: UUID, asset_id: UUID, quantity: Decimal, held: Decimal
    ) -> None:
        super().__init__(
            f"{self.code}: transaction {transaction_id} takes asset {asset_id} to {quantity}"
        )
        self.transaction_id = transaction_id
        self.asset_id = asset_id
        self.quantity = quantity  # what the transaction would leave
        self.held = held  # the quantity just before it (D-139)


class NegativeQuantityError(QuantityConflictError):
    """The replay would take an asset below zero."""

    code = ConflictCode.NEGATIVE_QUANTITY


class SplitToZeroError(QuantityConflictError):
    """A split would round a held quantity to zero, leaving cost without units (D-69)."""

    code = ConflictCode.SPLIT_TO_ZERO


class WarningCode(StrEnum):
    NEGATIVE_CASH = "NEGATIVE_CASH"
    INCOME_WITHOUT_HOLDING = "INCOME_WITHOUT_HOLDING"
    FUTURE_DATE = "FUTURE_DATE"


@dataclass(frozen=True, slots=True)
class LedgerWarning:
    code: WarningCode
    date: date
    transaction_id: UUID | None


@dataclass(frozen=True, slots=True)
class Position:
    """Per-asset state of an account after the replay. Amounts in account currency.

    Immutable: the replay builds a new value for every transaction, so a cached ledger cannot
    be changed by accident.
    """

    asset_id: UUID
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    cost_removed: Decimal = ZERO  # lifetime, base of realized % (D-41)
    realized_gross: Decimal = ZERO
    realized_taxes: Decimal = ZERO  # SELL taxes (D-42)
    income_gross: Decimal = ZERO  # dividends, coupons, accrued received - accrued paid (§6)
    income_taxes: Decimal = ZERO  # DIVIDEND/COUPON taxes and BUY taxes (D-42)
    ever_held: bool = False

    @property
    def pmc(self) -> Decimal | None:
        """Average cost per unit, display only: never fed back into calculations (D-40)."""
        return div8(self.cost_basis, self.quantity) if self.quantity else None

    @property
    @exact
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.realized_taxes

    @property
    def realized_pct_gross(self) -> Decimal | None:
        return ratio8(self.realized_gross, self.cost_removed)

    @property
    def realized_pct_net(self) -> Decimal | None:
        return ratio8(self.realized_net, self.cost_removed)

    @property
    @exact
    def income_net(self) -> Decimal:
        return self.income_gross - self.income_taxes

    @property
    @exact
    def taxes(self) -> Decimal:
        return self.income_taxes + self.realized_taxes


@dataclass(frozen=True, slots=True)
class Ledger:
    positions: Mapping[UUID, Position]  # read-only view
    cash: Decimal
    warnings: tuple[LedgerWarning, ...] = field(default=())
    as_of: date | None = None  # replayed up to and including this date; None = everything
    last_date: date | None = None  # date of the last replayed transaction


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
                pos = positions.get(tx.asset_id) or Position(tx.asset_id)
                positions[tx.asset_id] = _apply(tx, pos, warnings)
            cash += _cash_effect(tx)

        if current_day is not None:
            _check_cash(cash, current_day, cash_warned, warnings)

    return Ledger(MappingProxyType(positions), cash, tuple(warnings), as_of, current_day)


def future_dated(transactions: Iterable[Transaction], today: date) -> tuple[LedgerWarning, ...]:
    """One warning per transaction dated after `today` (§4): accepted, but likely a typo.

    Separate from `replay`, which knows no clock; the caller passes today's date.
    """
    return tuple(
        LedgerWarning(WarningCode.FUTURE_DATE, tx.trade_date, tx.id)
        for tx in replay_order(transactions)
        if tx.trade_date > today
    )


@exact
def cost_removed_for(cost_basis: Decimal, held: Decimal, sold: Decimal) -> Decimal:
    """Cost removed by selling `sold` of `held` units: cost basis x sold / held (D-40).

    Selling everything removes the remaining cost exactly (D-63). It is also the gross amount
    of the source SELL when moving a holding between accounts (§3.1, D-47).
    """
    if not 0 < sold <= held:
        raise ValueError(f"cannot sell {sold} of {held}")
    if sold == held:
        return cost_basis
    return mul_div8(cost_basis, sold, held)


def _apply(tx: Transaction, pos: Position, warnings: list[LedgerWarning]) -> Position:
    if tx.type is T.BUY:
        assert tx.quantity is not None and tx.gross_amount is not None
        return replace(
            pos,
            quantity=pos.quantity + tx.quantity,
            cost_basis=pos.cost_basis + tx.gross_amount + or_zero(tx.fees),
            income_gross=pos.income_gross - or_zero(tx.accrued_interest_paid),
            income_taxes=pos.income_taxes + or_zero(tx.taxes),
            ever_held=True,
        )
    if tx.type is T.SELL:
        assert tx.quantity is not None and tx.gross_amount is not None
        if tx.quantity > pos.quantity:
            raise NegativeQuantityError(
                tx.id, pos.asset_id, pos.quantity - tx.quantity, pos.quantity
            )
        removed = cost_removed_for(pos.cost_basis, pos.quantity, tx.quantity)
        return replace(
            pos,
            quantity=pos.quantity - tx.quantity,
            cost_basis=pos.cost_basis - removed,
            cost_removed=pos.cost_removed + removed,
            realized_gross=pos.realized_gross + tx.gross_amount - or_zero(tx.fees) - removed,
            realized_taxes=pos.realized_taxes + or_zero(tx.taxes),
            income_gross=pos.income_gross + or_zero(tx.accrued_interest_received),
        )
    if tx.type is T.STOCK_SPLIT:
        assert tx.split_ratio is not None
        quantity = mul_div8(pos.quantity, tx.split_ratio.new, tx.split_ratio.old)
        if pos.quantity > 0 and quantity == 0:
            raise SplitToZeroError(tx.id, pos.asset_id, quantity, pos.quantity)
        return replace(pos, quantity=quantity)
    assert tx.type in (T.DIVIDEND, T.COUPON) and tx.gross_amount is not None
    if not pos.ever_held:
        warnings.append(LedgerWarning(WarningCode.INCOME_WITHOUT_HOLDING, tx.trade_date, tx.id))
    return replace(
        pos,
        income_gross=pos.income_gross + tx.gross_amount,
        income_taxes=pos.income_taxes + or_zero(tx.taxes),
    )


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
