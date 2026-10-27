"""Allocation and income history of a view: one account, or all of them in the reporting
currency (domain-rules §9, §13; D-153).

Allocation: each security of each account counts at its value (at its cost when valued at
cost, D-46), converted at the latest rate; cash counts per account currency, and a currency
whose cash is negative is left out of the shares and reported apart. Income: dividends and
coupons only, by month, each account's month converted at the latest rate. An account without
a rate to the view's currency is left out (D-73); one with no transactions is ignored.
"""

from collections import defaultdict
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from portfolio_tracker.engine.decimals import div8, exact, or_zero
from portfolio_tracker.engine.model import ZERO, Asset, AssetType, Transaction, TransactionType
from portfolio_tracker.engine.valuation import AccountValuation, FxRates

CASH = "CASH"  # the category of cash in the allocation by type; no asset type has this name
MONTHS = 12  # the income history by month covers the last 12 months
_INCOME = (TransactionType.DIVIDEND, TransactionType.COUPON)


@dataclass(frozen=True, slots=True)
class Holding:
    """One security of one account, in the account currency."""

    asset_id: UUID
    asset_type: AssetType
    quote_currency: str
    value: Decimal | None  # None: its rate to the account currency is missing (D-64)


@dataclass(frozen=True, slots=True)
class AccountBreakdownInput:
    currency: str
    cash: Decimal
    holdings: tuple[Holding, ...]
    transactions: tuple[Transaction, ...]  # empty: an account with nothing in it


def breakdown_input(
    currency: str,
    valuation: AccountValuation,
    assets: Mapping[UUID, Asset],
    transactions: Iterable[Transaction],
) -> AccountBreakdownInput:
    holdings = tuple(
        Holding(asset_id, assets[asset_id].asset_type, assets[asset_id].quote_currency, p.value)
        for asset_id, p in valuation.positions.items()
    )
    return AccountBreakdownInput(currency, valuation.cash, holdings, tuple(transactions))


@dataclass(frozen=True, slots=True)
class Share[K: Hashable]:
    key: K
    value: Decimal
    share: Decimal  # value ÷ the sum of the counted parts, 8 decimals (§0)


@dataclass(frozen=True, slots=True)
class IncomeAmounts:
    gross: Decimal
    taxes: Decimal  # signed: positive withheld, negative a credit

    @property
    @exact
    def net(self) -> Decimal:
        return self.gross - self.taxes


@dataclass(frozen=True, slots=True)
class IncomeMonth:
    year: int
    month: int
    amounts: IncomeAmounts


@dataclass(frozen=True, slots=True)
class IncomeYear:
    year: int
    amounts: IncomeAmounts


@dataclass(frozen=True, slots=True)
class Breakdown:
    currency: str
    by_type: tuple[Share[str], ...]  # the asset types and CASH, largest first
    by_currency: tuple[Share[str], ...]
    by_asset: tuple[Share[UUID], ...]
    negative_cash: Decimal  # the negative cash left out of the shares (zero when none)
    income_by_month: tuple[IncomeMonth, ...]  # the last 12 months, oldest first
    income_by_year: tuple[IncomeYear, ...]  # every year from the first income, oldest first
    income_total: IncomeAmounts
    partial: bool
    unavailable_currencies: tuple[str, ...]  # account currencies without a rate (left out)


def _shares[K: Hashable](values: Mapping[K, Decimal], total: Decimal) -> tuple[Share[K], ...]:
    ranked = sorted(values.items(), key=lambda item: (-item[1], str(item[0])))
    # Only parts with a value get a share, so the total is never zero here.
    return tuple(Share(key, value, div8(value, total)) for key, value in ranked if value)


def _months_up_to(day: date) -> list[tuple[int, int]]:
    index = day.year * 12 + day.month - 1
    return [divmod(i, 12) for i in range(index - MONTHS + 1, index + 1)]


@exact
def breakdown(
    accounts: Iterable[AccountBreakdownInput], fx: FxRates, currency: str, day: date
) -> Breakdown:
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_currency: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_asset: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    cash: dict[str, Decimal] = defaultdict(lambda: ZERO)
    income: dict[tuple[int, int], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    unavailable: list[str] = []
    partial = False

    for account in accounts:
        if not account.transactions:
            continue  # nothing in it (D-153)
        if fx.convert(ZERO, account.currency, currency, day) is None:
            partial = True
            if account.currency not in unavailable:
                unavailable.append(account.currency)
            continue

        def converted(amount: Decimal, source: str = account.currency) -> Decimal:
            result = fx.convert(amount, source, currency, day)
            assert result is not None  # the account's rate exists: checked above
            return result

        for holding in account.holdings:
            if holding.value is None:
                partial = True
                continue
            if not holding.value:
                continue  # a closed position
            value = converted(holding.value)
            by_type[holding.asset_type.value] += value
            by_currency[holding.quote_currency] += value
            by_asset[holding.asset_id] += value
        cash[account.currency] += converted(account.cash)

        own: dict[tuple[int, int], list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
        for tx in account.transactions:
            if tx.type in _INCOME and tx.trade_date <= day:
                amounts = own[(tx.trade_date.year, tx.trade_date.month)]
                amounts[0] += or_zero(tx.gross_amount)
                amounts[1] += or_zero(tx.taxes)
        for key, (gross, taxes) in own.items():
            income[key][0] += converted(gross)
            income[key][1] += converted(taxes)

    negative_cash = sum((value for value in cash.values() if value < 0), ZERO)
    for code, value in cash.items():
        if value > 0:
            by_type[CASH] += value
            by_currency[code] += value
    total = sum(by_type.values(), ZERO)

    def amounts(keys: Iterable[tuple[int, int]]) -> IncomeAmounts:
        found = [income[k] for k in keys if k in income]
        return IncomeAmounts(sum((a[0] for a in found), ZERO), sum((a[1] for a in found), ZERO))

    months = tuple(
        IncomeMonth(year, month + 1, amounts([(year, month + 1)]))
        for year, month in _months_up_to(day)
    )
    first = min((year for year, _ in income), default=None)
    years = () if first is None else tuple(
        IncomeYear(year, amounts(k for k in income if k[0] == year))
        for year in range(first, day.year + 1)
    )  # fmt: skip
    return Breakdown(
        currency=currency,
        by_type=_shares(by_type, total),
        by_currency=_shares(by_currency, total),
        by_asset=_shares(by_asset, total),
        negative_cash=negative_cash,
        income_by_month=months,
        income_by_year=years,
        income_total=amounts(list(income)),
        partial=partial,
        unavailable_currencies=tuple(unavailable),
    )
