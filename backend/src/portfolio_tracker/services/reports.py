"""Derived figures computed on request by a fresh engine replay (domain-rules §5, §6, §9, §15).

Nothing derived is stored: the replay of the stored transactions and prices is the rebuild
path. Valuation day = the later of today (UTC) and the user's latest transaction date, so every
stored transaction counts (M2 step 10 answer). There is no FX table until M4: a position quoted
in another currency than its account is unavailable and the account partial (D-64), and an
account in another currency than the reporting one is left out of the portfolio (D-73).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker.db import models
from portfolio_tracker.engine.decimals import exact
from portfolio_tracker.engine.ledger import LedgerWarning, Position, replay
from portfolio_tracker.engine.portfolio import AccountInput, PortfolioFigures, aggregate
from portfolio_tracker.engine.valuation import (
    AccountValuation,
    FxRates,
    PositionValue,
    PriceHistory,
    value_account,
)
from portfolio_tracker.repositories import accounts, assets, mapping, prices
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services.transactions import all_warnings

NO_FX = FxRates({})  # the FX table and provider arrive in M4


@dataclass(frozen=True, slots=True)
class PositionReport:
    asset: models.Asset
    position: Position
    value: PositionValue
    price: Decimal | None  # close used for the value (quote unit), dated value.price_date

    @property
    @exact
    def gain_gross(self) -> Decimal | None:
        """unrealized + realized gross + income gross (§6); None when the value is unavailable."""
        unrealized = self.value.unrealized
        if unrealized is None:
            return None
        return unrealized + self.position.realized_gross + self.position.income_gross

    @property
    @exact
    def gain_net(self) -> Decimal | None:
        gross = self.gain_gross
        return None if gross is None else gross - self.position.taxes


@dataclass(frozen=True, slots=True)
class AccountReport:
    account: models.Account
    valuation: AccountValuation
    positions: list[PositionReport]  # sorted by asset name
    warnings: tuple[LedgerWarning, ...]

    @property
    def open_positions(self) -> int:
        return sum(1 for p in self.positions if p.position.quantity > 0)


def valuation_day(db: Session, owner_id: UUID, today: date) -> date:
    last = txs.last_trade_date(db, owner_id)
    return max(today, last) if last else today


def account_report(
    db: Session, owner_id: UUID, account: models.Account, today: date, day: date
) -> AccountReport:
    """Replay one already owner-checked account and value it on `day` (>= its last date)."""
    rows = txs.account_ledger(db, owner_id, account.id)
    transactions = [mapping.to_engine_transaction(r) for r in rows]
    ledger = replay(transactions, as_of=day)
    asset_rows = assets.assets_by_id(db, set(ledger.positions))
    histories = {
        asset_id: PriceHistory(closes)
        for asset_id, closes in prices.closes_for(db, owner_id, set(ledger.positions)).items()
    }
    valuation = value_account(
        ledger,
        {a.id: mapping.to_engine_asset(a) for a in asset_rows.values()},
        histories,
        NO_FX,
        account.currency,
        day,
    )
    positions = []
    for asset_id, position in ledger.positions.items():
        value = valuation.positions[asset_id]
        price = None
        if value.price_date is not None:  # the close the engine used (closed: none)
            price = histories[asset_id].on_or_before(day)[1]
        positions.append(PositionReport(asset_rows[asset_id], position, value, price))
    positions.sort(key=lambda p: (p.asset.name.casefold(), str(p.asset.id)))
    return AccountReport(account, valuation, positions, all_warnings(ledger, transactions, today))


@dataclass(frozen=True, slots=True)
class PortfolioReport:
    day: date
    figures: PortfolioFigures
    account_count: int


def portfolio_report(db: Session, user: models.User, today: date) -> PortfolioReport:
    """Every account of the user, aggregated in the reporting currency (§9, §13)."""
    day = valuation_day(db, user.id, today)
    inputs = [
        AccountInput(a.currency, account_report(db, user.id, a, today, day).valuation)
        for a in accounts.list_accounts(db, user.id)
    ]
    figures = aggregate(inputs, NO_FX, user.reporting_currency, day)
    return PortfolioReport(day, figures, len(inputs))
