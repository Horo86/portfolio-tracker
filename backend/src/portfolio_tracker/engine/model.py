"""Engine input types (domain-model.md). Plain immutable values, independent of the database."""

from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from portfolio_tracker.engine.decimals import check_decimal

ZERO = Decimal(0)


class TransactionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    COUPON = "COUPON"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    STOCK_SPLIT = "STOCK_SPLIT"


class AssetType(StrEnum):
    ETF = "ETF"
    STOCK = "STOCK"
    BOND = "BOND"


class QuoteUnit(StrEnum):
    CURRENCY = "CURRENCY"
    MINOR_CURRENCY_UNIT = "MINOR_CURRENCY_UNIT"
    PERCENT_OF_NOMINAL = "PERCENT_OF_NOMINAL"


@dataclass(frozen=True, slots=True)
class Asset:
    id: UUID
    asset_type: AssetType
    quote_currency: str
    quote_unit: QuoteUnit

    @property
    def is_bond(self) -> bool:
        return self.asset_type is AssetType.BOND


@dataclass(frozen=True, slots=True)
class SplitRatio:
    """Stock split `new:old` (2:1 doubles the quantity; 1:10 is a reverse split)."""

    new: Decimal
    old: Decimal

    def __post_init__(self) -> None:
        check_decimal("split_ratio.new", self.new)
        check_decimal("split_ratio.old", self.old)


# Decimal fields of a transaction; each must be a finite Decimal or None (D-32).
DECIMAL_FIELDS = (
    "quantity",
    "unit_price",
    "gross_amount",
    "fees",
    "accrued_interest_paid",
    "accrued_interest_received",
    "taxes",
    "total_amount",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Transaction:
    """One ledger entry of an account. Amounts are in the account currency (domain-rules §2).

    `sequence` is the insertion sequence, the last tie-breaker of the replay order (§4).
    Optional amounts are None when not given; None counts as 0 but a field that is not
    allowed for the type is rejected even when it is 0 (D-62).
    """

    id: UUID
    type: TransactionType
    trade_date: date
    sequence: int
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
    split_ratio: SplitRatio | None = None
    affects_cash: bool = True

    def __post_init__(self) -> None:
        for name in DECIMAL_FIELDS:
            check_decimal(name, getattr(self, name))
