"""Portfolio in the reporting currency (domain-rules §9, §13; D-36, D-64, D-73).

Already-computed account figures are converted: current figures at the latest rate on or before
the given day, the historical series at each day's rate. No historical FX attribution.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from portfolio_tracker.engine.decimals import exact, ratio8
from portfolio_tracker.engine.model import ZERO
from portfolio_tracker.engine.valuation import AccountValuation, FxRates

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
