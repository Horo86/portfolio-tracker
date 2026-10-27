"""Derived figures computed on request by a fresh engine replay (domain-rules §5, §6, §9, §15).

Nothing derived is stored: the replay of the stored transactions and prices is the rebuild
path. Valuation day = the later of today (UTC) and the user's latest transaction date, so every
stored transaction counts (M2 step 10 answer).

Prices: the owner's merged series (D-116, `prices.closes_for`). FX: the latest stored rate of
each currency on or before the valuation day (§9), converted through EUR and rounded once. A
missing rate leaves the position's value unavailable and the account partial (D-64), and an
account without a rate to the reporting currency out of the portfolio (D-73). **Notices**
(§12, D-120): a price older than 5 calendar days on the valuation day is stale; so is a rate,
and every conversion that used one names its pair and the date of the older rate.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from portfolio_tracker.db import models
from portfolio_tracker.engine.decimals import exact
from portfolio_tracker.engine.ledger import LedgerWarning, Position, replay
from portfolio_tracker.engine.model import Transaction
from portfolio_tracker.engine.portfolio import (
    AccountInput,
    AccountPositions,
    AggregatedPosition,
    PortfolioFigures,
    aggregate,
    aggregate_positions,
)
from portfolio_tracker.engine.valuation import (
    FX_BASE,
    AccountValuation,
    FxRates,
    PositionValue,
    PriceHistory,
    is_stale,
    value_account,
)
from portfolio_tracker.repositories import accounts, assets, fx, mapping, prices
from portfolio_tracker.repositories import transactions as txs
from portfolio_tracker.services.transactions import all_warnings


@dataclass(frozen=True, slots=True, order=True)
class FxNotice:
    """A conversion from `source` to `target` used a stale rate, the older one dated
    `rate_date` (D-120)."""

    source: str
    target: str
    rate_date: date


def stale_conversion(rates: FxRates, source: str, target: str, day: date) -> FxNotice | None:
    used = rates.conversion_date(source, target, day)
    return FxNotice(source, target, used) if used and is_stale(used, day) else None


def owner_rates(db: Session, owner_id: UUID, reporting_currency: str, day: date) -> FxRates:
    """The latest rate on or before `day` of every currency the owner's figures may need:
    account currencies, asset quote currencies and the reporting currency."""
    used = union(
        select(models.Account.currency).where(models.Account.owner_id == owner_id),
        select(models.Asset.quote_currency).where(models.Asset.owner_id == owner_id),
    )
    currencies = {code for (code,) in db.execute(used)} | {reporting_currency}
    return FxRates(fx.latest_rates(db, currencies - {FX_BASE}, day))


@dataclass(frozen=True, slots=True)
class PositionReport:
    asset: models.Asset
    position: Position
    value: PositionValue
    price: Decimal | None  # close used for the value (quote unit), dated value.price_date
    price_stale: bool  # that close is older than 5 days on the valuation day
    fx_notice: FxNotice | None  # its conversion to the account currency used a stale rate
    price_source: str | None  # PROVIDER or MANUAL: where that close comes from
    price_status: prices.PriceStatus  # where the asset's provider prices stand
    price_exchange: str | None = None  # PROVIDER: the exchange of its listing (D-152)

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
class ReportingValue:
    """The account's total value in the user's reporting currency (mockup 13): None when a
    rate is missing (D-73)."""

    currency: str
    total_value: Decimal | None
    fx_notice: FxNotice | None


@dataclass(frozen=True, slots=True)
class AccountReport:
    account: models.Account
    valuation: AccountValuation
    positions: list[PositionReport]  # sorted by asset name
    warnings: tuple[LedgerWarning, ...]
    reporting: ReportingValue
    transactions: tuple[Transaction, ...] = ()  # the account's ledger, in replay order

    @property
    def open_positions(self) -> int:
        return sum(1 for p in self.positions if p.position.quantity > 0)

    @property
    def fx_notices(self) -> tuple[FxNotice, ...]:
        """The stale conversions behind the account's figures, each pair once."""
        return tuple(sorted({p.fx_notice for p in self.positions if p.fx_notice}))


def valuation_day(db: Session, owner_id: UUID, today: date) -> date:
    last = txs.last_trade_date(db, owner_id)
    return max(today, last) if last else today


def account_report(
    db: Session,
    owner_id: UUID,
    account: models.Account,
    today: date,
    day: date,
    reporting_currency: str,
    rates: FxRates | None = None,
) -> AccountReport:
    """Replay one already owner-checked account and value it on `day` (>= its last date).
    `rates` defaults to `owner_rates`."""
    if rates is None:
        rates = owner_rates(db, owner_id, reporting_currency, day)
    rows = txs.account_ledger(db, owner_id, account.id)
    transactions = [mapping.to_engine_transaction(r) for r in rows]
    ledger = replay(transactions, as_of=day)
    asset_rows = assets.assets_by_id(db, set(ledger.positions))
    series = prices.price_series(db, owner_id, set(ledger.positions))
    histories = {asset_id: PriceHistory(s.closes) for asset_id, s in series.items()}
    statuses = prices.price_statuses(db, owner_id, set(ledger.positions))
    # An owner-less (shared) row, unused since D-116, reads only the owner's manual prices.
    manual_only = prices.PriceStatus(prices.PriceState.MANUAL)
    valuation = value_account(
        ledger,
        {a.id: mapping.to_engine_asset(a) for a in asset_rows.values()},
        histories,
        rates,
        account.currency,
        day,
    )
    positions = []
    for asset_id, position in ledger.positions.items():
        value, asset = valuation.positions[asset_id], asset_rows[asset_id]
        price, notice, source, exchange = None, None, None, None
        if value.price_date is not None:  # the close the engine used (closed: none)
            price = histories[asset_id].on_or_before(day)[1]
            source = "MANUAL" if value.price_date in series[asset_id].manual_days else "PROVIDER"
            exchange = series[asset_id].exchange if source == "PROVIDER" else None
            if value.value is not None:
                notice = stale_conversion(rates, asset.quote_currency, account.currency, day)
        stale = value.price_date is not None and is_stale(value.price_date, day)
        status = statuses.get(asset_id, manual_only)
        positions.append(
            PositionReport(asset, position, value, price, stale, notice, source, status, exchange)
        )
    positions.sort(key=lambda p: (p.asset.name.casefold(), str(p.asset.id)))
    reporting = ReportingValue(
        reporting_currency,
        rates.convert(valuation.total_value, account.currency, reporting_currency, day),
        stale_conversion(rates, account.currency, reporting_currency, day),
    )
    warnings = all_warnings(ledger, transactions, today)
    return AccountReport(account, valuation, positions, warnings, reporting, tuple(transactions))


@dataclass(frozen=True, slots=True)
class PortfolioReport:
    day: date
    figures: PortfolioFigures
    account_count: int
    fx_notices: tuple[FxNotice, ...]  # stale conversions behind any portfolio figure


def portfolio_report(db: Session, user: models.User, today: date) -> PortfolioReport:
    """Every account of the user, aggregated in the reporting currency (§9, §13)."""
    day = valuation_day(db, user.id, today)
    currency = user.reporting_currency
    rates = owner_rates(db, user.id, currency, day)
    reports = [
        account_report(db, user.id, a, today, day, currency, rates)
        for a in accounts.list_accounts(db, user.id)
    ]
    figures = aggregate(
        [AccountInput(r.account.currency, r.valuation, bool(r.transactions)) for r in reports],
        rates,
        currency,
        day,
    )
    notices: set[FxNotice] = set()
    for report in reports:
        if report.reporting.total_value is None:
            continue  # left out of the portfolio (D-73): its figures are not in it
        notices.update(report.fx_notices)
        if report.reporting.fx_notice is not None:
            notices.add(report.reporting.fx_notice)
    return PortfolioReport(day, figures, len(reports), tuple(sorted(notices)))


@dataclass(frozen=True, slots=True)
class HoldingReport:
    """One asset over all accounts, in the reporting currency (D-143). The price fields belong
    to the asset and are the same in every account."""

    asset: models.Asset
    holding: AggregatedPosition
    price: Decimal | None
    price_date: date | None
    price_stale: bool
    price_source: str | None
    price_status: prices.PriceStatus
    fx_notices: tuple[FxNotice, ...]  # stale conversions behind its figures
    price_exchange: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioPositions:
    day: date
    currency: str
    holdings: list[HoldingReport]  # sorted by asset name
    partial: bool
    unavailable_currencies: tuple[str, ...]

    @property
    def fx_notices(self) -> tuple[FxNotice, ...]:
        return tuple(sorted({n for h in self.holdings for n in h.fx_notices}))


def portfolio_positions(
    db: Session, user: models.User, today: date, include_closed: bool
) -> PortfolioPositions:
    """Every account's positions summed per asset in the reporting currency (D-143). `partial`
    and the stale-rate notices count only the holdings returned."""
    day = valuation_day(db, user.id, today)
    currency = user.reporting_currency
    rates = owner_rates(db, user.id, currency, day)
    reports = [
        account_report(db, user.id, a, today, day, currency, rates)
        for a in accounts.list_accounts(db, user.id)
    ]
    aggregated = aggregate_positions(
        [
            AccountPositions(
                r.account.id,
                r.account.currency,
                {p.asset.id: p.position for p in r.positions},
                r.valuation.positions,
            )
            for r in reports
        ],
        rates,
        currency,
        day,
    )
    parts: dict[UUID, list[tuple[AccountReport, PositionReport]]] = {}
    for report in reports:
        for position in report.positions:
            parts.setdefault(position.asset.id, []).append((report, position))
    holdings = []
    for asset_id, holding in aggregated.positions.items():
        if not include_closed and holding.quantity == 0:
            continue
        summed = set(holding.account_ids)
        included = [(r, p) for r, p in parts[asset_id] if r.account.id in summed]
        priced = next((p for _, p in included if p.value.price_date is not None), included[0][1])
        notices = {p.fx_notice for _, p in included if p.fx_notice} | {
            n
            for r, _ in included
            if (n := stale_conversion(rates, r.account.currency, currency, day))
        }
        holdings.append(
            HoldingReport(
                asset=priced.asset,
                holding=holding,
                price=priced.price,
                price_date=priced.value.price_date,
                price_stale=priced.price_stale,
                price_source=priced.price_source,
                price_status=priced.price_status,
                fx_notices=tuple(sorted(notices)),
                price_exchange=priced.price_exchange,
            )
        )
    holdings.sort(key=lambda h: (h.asset.name.casefold(), str(h.asset.id)))
    unavailable = aggregated.unavailable_currencies
    partial = bool(unavailable) or any(h.holding.partial for h in holdings)
    return PortfolioPositions(day, currency, holdings, partial, unavailable)
