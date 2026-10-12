"""Account totals of realized gain, income and taxes over all positions (domain-rules §5, §6).

Unrealized figures need prices and live in `valuation`.
"""

from dataclasses import dataclass
from decimal import Decimal

from portfolio_tracker.engine.decimals import engine_context, ratio8
from portfolio_tracker.engine.ledger import Ledger


@dataclass(frozen=True, slots=True)
class AccountTotals:
    invested_capital: Decimal  # cost basis of open positions (D-11)
    cost_removed: Decimal
    realized_gross: Decimal
    realized_taxes: Decimal
    income_gross: Decimal
    income_taxes: Decimal

    @property
    def realized_net(self) -> Decimal:
        return self.realized_gross - self.realized_taxes

    @property
    def realized_pct_gross(self) -> Decimal | None:
        return ratio8(self.realized_gross, self.cost_removed)

    @property
    def realized_pct_net(self) -> Decimal | None:
        return ratio8(self.realized_net, self.cost_removed)

    @property
    def income_net(self) -> Decimal:
        return self.income_gross - self.income_taxes

    @property
    def taxes(self) -> Decimal:
        return self.income_taxes + self.realized_taxes


def summarize(ledger: Ledger) -> AccountTotals:
    positions = ledger.positions.values()
    with engine_context():
        return AccountTotals(
            invested_capital=sum((p.cost_basis for p in positions), Decimal(0)),
            cost_removed=sum((p.cost_removed for p in positions), Decimal(0)),
            realized_gross=sum((p.realized_gross for p in positions), Decimal(0)),
            realized_taxes=sum((p.realized_taxes for p in positions), Decimal(0)),
            income_gross=sum((p.income_gross for p in positions), Decimal(0)),
            income_taxes=sum((p.income_taxes for p in positions), Decimal(0)),
        )
