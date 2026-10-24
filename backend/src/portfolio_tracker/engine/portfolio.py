"""Portfolio in the reporting currency (domain-rules §9, §13; D-36, D-64, D-73).

Already-computed account figures are converted: current figures at the latest rate on or before
the given day, the historical series at each day's rate. No historical FX attribution.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from portfolio_tracker.engine.decimals import div8, exact, ratio8
from portfolio_tracker.engine.ledger import Position
from portfolio_tracker.engine.model import ZERO
from portfolio_tracker.engine.valuation import AccountValuation, FxRates, PositionValue

_FIGURES = (
    "securities_value",
    "cash",
    "invested_capital",
    "unrealized",
    "realized_gross",
    "realized_taxes",
    "income_gross",
    "income_taxes",
    "cost_removed",
    "valued_cost",
)


@dataclass(frozen=True, slots=True)
class AccountInput:
    currency: str
    valuation: AccountValuation


@dataclass(frozen=True, slots=True)
class PortfolioFigures:
    """Current figures in the reporting currency. `partial` = some part could not be valued."""

    currency: str
    securities_value: Decimal
    cash: Decimal
    invested_capital: Decimal
    unrealized: Decimal
    realized_gross: Decimal
    realized_taxes: Decimal
    income_gross: Decimal
    income_taxes: Decimal
    cost_removed: Decimal  # base of realized % (D-41)
    valued_cost: Decimal  # base of unrealized %: cost of the valued positions (D-64)
    partial: bool
    unavailable_currencies: tuple[str, ...]  # account currencies without a rate to `currency`

    @property
    @exact
    def total_value(self) -> Decimal:
        return self.securities_value + self.cash

    @property
    @exact
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.realized_taxes

    @property
    @exact
    def income_net(self) -> Decimal:
        return self.income_gross - self.income_taxes

    @property
    @exact
    def taxes(self) -> Decimal:
        return self.realized_taxes + self.income_taxes

    @property
    def realized_pct_gross(self) -> Decimal | None:
        return ratio8(self.realized_gross, self.cost_removed)

    @property
    def realized_pct_net(self) -> Decimal | None:
        return ratio8(self.realized_net, self.cost_removed)

    @property
    def unrealized_pct(self) -> Decimal | None:
        return ratio8(self.unrealized, self.valued_cost)

    @property
    @exact
    def gain_gross(self) -> Decimal:
        return self.unrealized + self.realized_gross + self.income_gross

    @property
    @exact
    def gain_net(self) -> Decimal:
        return self.gain_gross - self.taxes


def _account_figures(valuation: AccountValuation) -> dict[str, Decimal]:
    totals = valuation.totals
    return {
        "securities_value": valuation.securities_value,
        "cash": valuation.cash,
        "invested_capital": totals.invested_capital,
        "unrealized": valuation.unrealized,
        "realized_gross": totals.realized_gross,
        "realized_taxes": totals.realized_taxes,
        "income_gross": totals.income_gross,
        "income_taxes": totals.income_taxes,
        "cost_removed": totals.cost_removed,
        "valued_cost": valuation.valued_cost,
    }


@exact
def aggregate(
    accounts: Iterable[AccountInput], fx: FxRates, currency: str, day: date
) -> PortfolioFigures:
    """Sum the accounts' figures converted at the latest rate on or before `day`."""
    sums = dict.fromkeys(_FIGURES, ZERO)
    partial = False
    unavailable: list[str] = []
    for account in accounts:
        figures = _account_figures(account.valuation)
        converted = {k: fx.convert(v, account.currency, currency, day) for k, v in figures.items()}
        if any(v is None for v in converted.values()):
            partial = True
            if account.currency not in unavailable:
                unavailable.append(account.currency)
            continue
        partial = partial or account.valuation.partial
        for key, value in converted.items():
            assert value is not None
            sums[key] += value
    return PortfolioFigures(
        currency=currency, partial=partial, unavailable_currencies=tuple(unavailable), **sums
    )


@exact
def portfolio_value_on(
    accounts: Iterable[AccountInput], fx: FxRates, currency: str, day: date
) -> tuple[Decimal, bool]:
    """One point of the historical series: Σ account value converted at that day's rate.

    Returns (value, partial); accounts without a rate are left out and make the point partial.
    """
    total, partial = ZERO, False
    for account in accounts:
        value = fx.convert(account.valuation.total_value, account.currency, currency, day)
        if value is None:
            partial = True
            continue
        total += value
        partial = partial or account.valuation.partial
    return total, partial


@dataclass(frozen=True, slots=True)
class AccountPositions:
    """One account's positions (the ledger) and their values, in the account currency."""

    account_id: UUID
    currency: str
    positions: Mapping[UUID, Position]
    values: Mapping[UUID, PositionValue]


_POSITION_FIGURES = (
    "cost_basis",
    "cost_removed",
    "realized_gross",
    "realized_taxes",
    "income_gross",
    "income_taxes",
)


@dataclass(frozen=True, slots=True)
class AggregatedPosition:
    """One asset over all accounts, in the reporting currency (D-143). `value` sums the valued
    parts (None when no part is valued); `partial` = some part could not be valued."""

    asset_id: UUID
    account_ids: tuple[UUID, ...]
    quantity: Decimal
    cost_basis: Decimal
    cost_removed: Decimal  # base of realized % (D-41)
    realized_gross: Decimal
    realized_taxes: Decimal
    income_gross: Decimal
    income_taxes: Decimal
    value: Decimal | None
    valued_cost: Decimal  # cost of the valued parts: base of unrealized % (D-64)
    estimated_at_cost: bool  # some part has no price (D-46)
    partial: bool

    @property
    def pmc(self) -> Decimal | None:
        """Average cost per unit, display only (D-40); per unit of nominal for a bond."""
        return div8(self.cost_basis, self.quantity) if self.quantity else None

    @property
    @exact
    def unrealized(self) -> Decimal | None:
        return None if self.value is None else self.value - self.valued_cost

    @property
    def unrealized_pct(self) -> Decimal | None:
        unrealized = self.unrealized
        return None if unrealized is None else ratio8(unrealized, self.valued_cost)

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
        return self.realized_taxes + self.income_taxes

    @property
    @exact
    def gain_gross(self) -> Decimal | None:
        """Over the valued parts, like a partial account (D-73); None when none is valued."""
        unrealized = self.unrealized
        if unrealized is None:
            return None
        return unrealized + self.realized_gross + self.income_gross

    @property
    @exact
    def gain_net(self) -> Decimal | None:
        gross = self.gain_gross
        return None if gross is None else gross - self.taxes


@dataclass(frozen=True, slots=True)
class AggregatedPositions:
    currency: str
    positions: dict[UUID, AggregatedPosition]
    partial: bool
    unavailable_currencies: tuple[str, ...]  # account currencies without a rate to `currency`


@dataclass(frozen=True, slots=True)
class _Part:
    """One account's share of an asset, converted."""

    account_id: UUID
    quantity: Decimal
    figures: dict[str, Decimal]
    value: Decimal | None
    estimated_at_cost: bool


@exact
def aggregate_positions(
    accounts: Iterable[AccountPositions], fx: FxRates, currency: str, day: date
) -> AggregatedPositions:
    """One row per asset over all accounts (D-143): each account's figures converted at the
    latest rate on or before `day`, as `aggregate` does, then summed. An account without a rate
    to `currency` is left out entirely (D-73)."""
    parts: dict[UUID, list[_Part]] = {}
    unavailable: list[str] = []
    for account in accounts:
        if fx.convert(ZERO, account.currency, currency, day) is None:
            if account.currency not in unavailable:
                unavailable.append(account.currency)
            continue
        for asset_id, position in account.positions.items():
            parts.setdefault(asset_id, []).append(
                _part(account, position, account.values[asset_id], fx, currency, day)
            )
    positions = {asset_id: _sum(asset_id, shares) for asset_id, shares in parts.items()}
    return AggregatedPositions(
        currency=currency,
        positions=positions,
        partial=bool(unavailable) or any(p.partial for p in positions.values()),
        unavailable_currencies=tuple(unavailable),
    )


def _part(
    account: AccountPositions,
    position: Position,
    value: PositionValue,
    fx: FxRates,
    currency: str,
    day: date,
) -> _Part:
    def converted(amount: Decimal) -> Decimal:
        result = fx.convert(amount, account.currency, currency, day)
        assert result is not None  # the account's rate exists: checked by the caller
        return result

    return _Part(
        account_id=account.account_id,
        quantity=position.quantity,
        figures={name: converted(getattr(position, name)) for name in _POSITION_FIGURES},
        value=None if value.value is None else converted(value.value),
        estimated_at_cost=value.estimated_at_cost,
    )


def _sum(asset_id: UUID, parts: list[_Part]) -> AggregatedPosition:
    values = [p.value for p in parts if p.value is not None]
    valued_cost = (p.figures["cost_basis"] for p in parts if p.value is not None)
    sums = {name: sum((p.figures[name] for p in parts), ZERO) for name in _POSITION_FIGURES}
    return AggregatedPosition(
        asset_id=asset_id,
        account_ids=tuple(p.account_id for p in parts),
        quantity=sum((p.quantity for p in parts), ZERO),
        value=sum(values, ZERO) if values else None,
        valued_cost=sum(valued_cost, ZERO),
        estimated_at_cost=any(p.estimated_at_cost for p in parts),
        partial=len(values) < len(parts),
        **sums,
    )
