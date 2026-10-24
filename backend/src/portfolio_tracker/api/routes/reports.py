"""Derived figures of the logged-in user: account summary and positions (mockups 13, 02),
portfolio summary in the reporting currency (mockup 01). Computed on request by a fresh
replay, never stored (domain-rules §15).

Amounts are decimal strings in the account currency (portfolio: reporting currency).
Percentages are ratios (`0.1` = +10%), null when their base is zero. `partial` marks totals
that leave something out for a missing FX rate and must carry a visible label (D-64, D-73).
`price_stale` marks a close older than 5 days on the valuation day, and `stale_fx` names each
conversion that used a rate older than 5 days, with the date of that rate (§12, D-120).
"""

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from portfolio_tracker.api.deps import CurrentAuth, DbSession, Now
from portfolio_tracker.api.routes.accounts import owned_account
from portfolio_tracker.api.schemas import DecimalStr
from portfolio_tracker.engine.ledger import Position
from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.engine.portfolio import PortfolioFigures
from portfolio_tracker.engine.valuation import AccountValuation
from portfolio_tracker.services.reports import (
    AccountReport,
    FxNotice,
    PositionReport,
    account_report,
    portfolio_report,
    valuation_day,
)

router = APIRouter(tags=["reports"])


class Figures(BaseModel):
    """Totals shared by an account and the portfolio (§5, §6, §14)."""

    currency: str
    day: date  # valuation day: the later of today (UTC) and the latest transaction date
    total_value: DecimalStr
    securities_value: DecimalStr
    cash: DecimalStr
    invested_capital: DecimalStr  # cost basis of open positions (D-11)
    valued_cost: DecimalStr  # base of unrealized %: equals invested_capital unless partial
    unrealized: DecimalStr
    unrealized_pct: DecimalStr | None
    realized_gross: DecimalStr
    realized_taxes: DecimalStr
    realized_net: DecimalStr
    realized_pct_gross: DecimalStr | None
    realized_pct_net: DecimalStr | None
    income_gross: DecimalStr
    income_taxes: DecimalStr
    income_net: DecimalStr
    taxes: DecimalStr
    gain_gross: DecimalStr
    gain_net: DecimalStr
    partial: bool


class StaleFx(BaseModel):
    """A conversion from `from_currency` to `to_currency` used a rate dated `rate_date`, older
    than 5 days on the valuation day (D-120)."""

    from_currency: str
    to_currency: str
    rate_date: date


def _stale_fx(notice: FxNotice | None) -> StaleFx | None:
    if notice is None:
        return None
    return StaleFx(
        from_currency=notice.source, to_currency=notice.target, rate_date=notice.rate_date
    )


class ReportingValueOut(BaseModel):
    """The account's total value in the user's reporting currency (mockup 13); null when a
    rate is missing (D-73)."""

    currency: str
    total_value: DecimalStr | None
    stale_fx: StaleFx | None


class SummaryWarning(BaseModel):
    code: str  # NEGATIVE_CASH | INCOME_WITHOUT_HOLDING | FUTURE_DATE
    date: date
    transaction_id: UUID | None


class AccountSummaryOut(Figures):
    account_id: UUID
    open_positions: int
    warnings: list[SummaryWarning]
    stale_fx: list[StaleFx]  # conversions of positions to the account currency
    reporting: ReportingValueOut


class PortfolioSummaryOut(Figures):
    account_count: int
    unavailable_currencies: list[str]  # account currencies without a rate (left out, D-73)
    stale_fx: list[StaleFx]  # every stale conversion behind a portfolio figure


class PositionOut(BaseModel):
    asset_id: UUID
    name: str
    ticker: str | None
    isin: str | None
    asset_type: str
    quote_currency: str
    quote_unit: str
    open: bool
    quantity: DecimalStr  # bonds: nominal
    pmc: DecimalStr | None  # average cost; bonds as % of nominal (D-66)
    price: DecimalStr | None  # close used, in the quote unit
    price_date: date | None
    price_stale: bool  # "price not updated since <price_date>" (§12)
    price_source: str | None  # PROVIDER | MANUAL: where the close used comes from (D-116)
    # OK | MANUAL (no ISIN: manual prices only) | PENDING | NOT_AVAILABLE | MISMATCH | FAILING:
    # where the asset's provider prices stand (D-119, D-121, D-134)
    price_status: str
    price_status_detail: str | None  # MISMATCH: CURRENCY | UNIT | LEVEL; FAILING: NOT_LISTED
    fx_stale: StaleFx | None  # its value was converted with a stale rate (D-120)
    estimated_at_cost: bool  # no price on or before the day (D-46)
    value_unavailable: bool  # missing FX: value, unrealized and gain are null (D-64)
    value: DecimalStr | None
    cost_basis: DecimalStr
    unrealized: DecimalStr | None
    unrealized_pct: DecimalStr | None
    realized_gross: DecimalStr
    realized_taxes: DecimalStr
    realized_net: DecimalStr
    realized_pct_gross: DecimalStr | None
    realized_pct_net: DecimalStr | None
    income_gross: DecimalStr
    income_taxes: DecimalStr
    income_net: DecimalStr
    taxes: DecimalStr
    gain_gross: DecimalStr | None
    gain_net: DecimalStr | None


def _account_figures(currency: str, v: AccountValuation) -> dict:
    t = v.totals
    return {
        "currency": currency, "day": v.day, "total_value": v.total_value,
        "securities_value": v.securities_value, "cash": v.cash,
        "invested_capital": t.invested_capital, "valued_cost": v.valued_cost,
        "unrealized": v.unrealized, "unrealized_pct": v.unrealized_pct,
        "realized_gross": t.realized_gross, "realized_taxes": t.realized_taxes,
        "realized_net": t.realized_net, "realized_pct_gross": t.realized_pct_gross,
        "realized_pct_net": t.realized_pct_net, "income_gross": t.income_gross,
        "income_taxes": t.income_taxes, "income_net": t.income_net, "taxes": t.taxes,
        "gain_gross": v.gain_gross, "gain_net": v.gain_net, "partial": v.partial,
    }  # fmt: skip


def _portfolio_figures(day: date, f: PortfolioFigures) -> dict:
    names = (
        "currency", "total_value", "securities_value", "cash", "invested_capital", "valued_cost",
        "unrealized", "unrealized_pct", "realized_gross", "realized_taxes", "realized_net",
        "realized_pct_gross", "realized_pct_net", "income_gross", "income_taxes", "income_net",
        "taxes", "gain_gross", "gain_net", "partial",
    )  # fmt: skip
    return {name: getattr(f, name) for name in names} | {"day": day}


def shown_pmc(position: Position, asset_type: AssetType) -> Decimal | None:
    """The average cost as the API shows it: a bond's per unit of nominal becomes % of
    nominal (D-66); exact."""
    pmc = position.pmc
    if pmc is not None and asset_type == AssetType.BOND:
        pmc = pmc * 100
    return pmc


def _position_out(p: PositionReport) -> PositionOut:
    pos, value, asset = p.position, p.value, p.asset
    pmc = shown_pmc(pos, asset.asset_type)
    return PositionOut(
        asset_id=asset.id, name=asset.name, ticker=asset.ticker, isin=asset.isin,
        asset_type=asset.asset_type, quote_currency=asset.quote_currency,
        quote_unit=asset.quote_unit, open=pos.quantity > 0, quantity=pos.quantity, pmc=pmc,
        price=p.price, price_date=value.price_date, price_stale=p.price_stale,
        price_source=p.price_source, price_status=p.price_status.state.value,
        price_status_detail=p.price_status.detail, fx_stale=_stale_fx(p.fx_notice),
        estimated_at_cost=value.estimated_at_cost,
        value_unavailable=value.value is None, value=value.value, cost_basis=pos.cost_basis,
        unrealized=value.unrealized, unrealized_pct=value.unrealized_pct,
        realized_gross=pos.realized_gross, realized_taxes=pos.realized_taxes,
        realized_net=pos.realized_net, realized_pct_gross=pos.realized_pct_gross,
        realized_pct_net=pos.realized_pct_net, income_gross=pos.income_gross,
        income_taxes=pos.income_taxes, income_net=pos.income_net, taxes=pos.taxes,
        gain_gross=p.gain_gross, gain_net=p.gain_net,
    )  # fmt: skip


def _report(account_id: UUID, auth, db, at) -> AccountReport:
    account = owned_account(db, auth, account_id)
    today = at.date()
    day = valuation_day(db, auth.user.id, today)
    return account_report(db, auth.user.id, account, today, day, auth.user.reporting_currency)


@router.get("/accounts/{account_id}/summary")
def account_summary(
    account_id: UUID, auth: CurrentAuth, db: DbSession, at: Now
) -> AccountSummaryOut:
    """Account figures in the account currency (D-36), with the ledger's current warnings."""
    report = _report(account_id, auth, db, at)
    return AccountSummaryOut(
        **_account_figures(report.account.currency, report.valuation),
        account_id=report.account.id,
        open_positions=report.open_positions,
        warnings=[
            SummaryWarning(code=w.code.value, date=w.date, transaction_id=w.transaction_id)
            for w in report.warnings
        ],
        stale_fx=[_stale_fx(n) for n in report.fx_notices],
        reporting=ReportingValueOut(
            currency=report.reporting.currency,
            total_value=report.reporting.total_value,
            stale_fx=_stale_fx(report.reporting.fx_notice),
        ),
    )


@router.get("/accounts/{account_id}/positions")
def account_positions(
    account_id: UUID,
    auth: CurrentAuth,
    db: DbSession,
    at: Now,
    include_closed: Annotated[bool, Query()] = False,
) -> list[PositionOut]:
    """One row per asset of the account, sorted by name. Closed positions (quantity 0, also
    income-only ones) only with `include_closed`."""
    report = _report(account_id, auth, db, at)
    return [_position_out(p) for p in report.positions if include_closed or p.position.quantity > 0]


@router.get("/portfolio/summary")
def portfolio_summary(auth: CurrentAuth, db: DbSession, at: Now) -> PortfolioSummaryOut:
    """Every account of the user in the reporting currency (§9, D-36, D-73), at the latest
    stored rates; an account without a rate is left out and the total is partial."""
    report = portfolio_report(db, auth.user, at.date())
    return PortfolioSummaryOut(
        **_portfolio_figures(report.day, report.figures),
        account_count=report.account_count,
        unavailable_currencies=list(report.figures.unavailable_currencies),
        stale_fx=[_stale_fx(n) for n in report.fx_notices],
    )
