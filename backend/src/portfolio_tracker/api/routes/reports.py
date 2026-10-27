"""Derived figures of the logged-in user: account summary and positions (mockups 13, 02),
portfolio summary in the reporting currency and the breakdown of a view (mockup 01, D-153).
Computed on request by a fresh replay, never stored (domain-rules §15).

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
from portfolio_tracker.engine.breakdown import IncomeAmounts
from portfolio_tracker.engine.ledger import Position
from portfolio_tracker.engine.model import AssetType
from portfolio_tracker.engine.portfolio import AggregatedPosition, PortfolioFigures
from portfolio_tracker.engine.valuation import AccountValuation
from portfolio_tracker.services.reports import (
    AccountReport,
    BreakdownReport,
    FxNotice,
    HoldingReport,
    PositionReport,
    account_breakdown,
    account_report,
    portfolio_breakdown,
    portfolio_positions,
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


def _notice(notice: FxNotice) -> StaleFx:
    return StaleFx(
        from_currency=notice.source, to_currency=notice.target, rate_date=notice.rate_date
    )


def _stale_fx(notice: FxNotice | None) -> StaleFx | None:
    return None if notice is None else _notice(notice)


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


class PositionFields(BaseModel):
    """A holding: in one account (account currency) or over all accounts (reporting
    currency, D-143)."""

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
    # PROVIDER: the exchange name, as the engine reports it, of the listing whose close is
    # used; the same for every user linked to the instrument (D-152, D-116)
    price_exchange: str | None
    # OK | MANUAL (no ISIN: manual prices only) | PENDING | NOT_AVAILABLE | MISMATCH | FAILING:
    # where the asset's provider prices stand (D-119, D-121, D-134)
    price_status: str
    price_status_detail: str | None  # MISMATCH: CURRENCY | UNIT | LEVEL; FAILING: NOT_LISTED
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


class PositionOut(PositionFields):
    fx_stale: StaleFx | None  # its value was converted with a stale rate (D-120)


class HoldingOut(PositionFields):
    account_ids: list[UUID]  # the accounts it is summed over
    valued_cost: DecimalStr  # base of unrealized %: the cost of the valued parts (D-64)
    partial: bool  # a part could not be valued: value, unrealized and gain sum the others
    stale_fx: list[StaleFx]  # stale conversions behind its figures (D-120)


class PortfolioPositionsOut(BaseModel):
    currency: str  # the reporting currency of every amount
    day: date
    partial: bool  # an account was left out (no rate, D-73) or a row is partial
    unavailable_currencies: list[str]  # account currencies without a rate (left out)
    stale_fx: list[StaleFx]
    positions: list[HoldingOut]  # sorted by name


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


def shown_pmc(position: Position | AggregatedPosition, asset_type: AssetType) -> Decimal | None:
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
        price_source=p.price_source, price_exchange=p.price_exchange,
        price_status=p.price_status.state.value,
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


def _holding_out(h: HoldingReport) -> HoldingOut:
    held, asset = h.holding, h.asset
    return HoldingOut(
        asset_id=asset.id, name=asset.name, ticker=asset.ticker, isin=asset.isin,
        asset_type=asset.asset_type, quote_currency=asset.quote_currency,
        quote_unit=asset.quote_unit, open=held.quantity > 0, quantity=held.quantity,
        pmc=shown_pmc(held, asset.asset_type), price=h.price, price_date=h.price_date,
        price_stale=h.price_stale, price_source=h.price_source, price_exchange=h.price_exchange,
        price_status=h.price_status.state.value, price_status_detail=h.price_status.detail,
        estimated_at_cost=held.estimated_at_cost, value_unavailable=held.value is None,
        value=held.value, cost_basis=held.cost_basis, unrealized=held.unrealized,
        unrealized_pct=held.unrealized_pct, realized_gross=held.realized_gross,
        realized_taxes=held.realized_taxes, realized_net=held.realized_net,
        realized_pct_gross=held.realized_pct_gross, realized_pct_net=held.realized_pct_net,
        income_gross=held.income_gross, income_taxes=held.income_taxes,
        income_net=held.income_net, taxes=held.taxes, gain_gross=held.gain_gross,
        gain_net=held.gain_net, account_ids=list(held.account_ids),
        valued_cost=held.valued_cost, partial=held.partial,
        stale_fx=[_notice(n) for n in h.fx_notices],
    )  # fmt: skip


@router.get("/portfolio/positions")
def portfolio_holdings(
    auth: CurrentAuth,
    db: DbSession,
    at: Now,
    include_closed: Annotated[bool, Query()] = False,
) -> PortfolioPositionsOut:
    """One row per asset over all accounts, in the reporting currency (D-143): each account's
    figures converted at the latest rate and summed; an account without a rate is left out
    (D-73). Closed positions only with `include_closed`."""
    report = portfolio_positions(db, auth.user, at.date(), include_closed)
    return PortfolioPositionsOut(
        currency=report.currency,
        day=report.day,
        partial=report.partial,
        unavailable_currencies=list(report.unavailable_currencies),
        stale_fx=[_notice(n) for n in report.fx_notices],
        positions=[_holding_out(h) for h in report.holdings],
    )


class ShareOut(BaseModel):
    key: str  # an asset type or CASH; a currency
    value: DecimalStr
    share: DecimalStr  # of the counted parts


class AssetShareOut(BaseModel):
    asset_id: UUID
    name: str
    value: DecimalStr
    share: DecimalStr


class IncomeOut(BaseModel):
    gross: DecimalStr
    taxes: DecimalStr  # signed: positive withheld, negative a credit
    net: DecimalStr


class IncomeMonthOut(IncomeOut):
    month: str  # YYYY-MM


class IncomeYearOut(IncomeOut):
    year: int


class BreakdownOut(BaseModel):
    """Allocation and income history of the view (D-153, §13), in its currency."""

    currency: str
    day: date
    partial: bool  # a security or an account could not be valued: left out of the shares
    unavailable_currencies: list[str]  # account currencies without a rate (left out, D-73)
    by_type: list[ShareOut]  # the asset types and CASH, largest first
    by_currency: list[ShareOut]  # securities by quote currency, cash by account currency
    by_asset: list[AssetShareOut]
    negative_cash: DecimalStr  # cash left out of the shares because it is negative
    income_by_month: list[IncomeMonthOut]  # dividends and coupons, the last 12 months
    income_by_year: list[IncomeYearOut]  # every year from the first income
    income_total: IncomeOut


def _income(a: IncomeAmounts) -> dict:
    return {"gross": a.gross, "taxes": a.taxes, "net": a.net}


def _breakdown_out(report: BreakdownReport) -> BreakdownOut:
    b = report.breakdown
    return BreakdownOut(
        currency=b.currency,
        day=report.day,
        partial=b.partial,
        unavailable_currencies=list(b.unavailable_currencies),
        by_type=[ShareOut(key=s.key, value=s.value, share=s.share) for s in b.by_type],
        by_currency=[ShareOut(key=s.key, value=s.value, share=s.share) for s in b.by_currency],
        by_asset=[
            AssetShareOut(asset_id=s.key, name=report.names[s.key], value=s.value, share=s.share)
            for s in b.by_asset
        ],
        negative_cash=b.negative_cash,
        income_by_month=[
            IncomeMonthOut(month=f"{m.year:04d}-{m.month:02d}", **_income(m.amounts))
            for m in b.income_by_month
        ],
        income_by_year=[IncomeYearOut(year=y.year, **_income(y.amounts)) for y in b.income_by_year],
        income_total=IncomeOut(**_income(b.income_total)),
    )


@router.get("/accounts/{account_id}/breakdown")
def account_breakdown_route(
    account_id: UUID, auth: CurrentAuth, db: DbSession, at: Now
) -> BreakdownOut:
    """Allocation and income history of one account, in its currency (D-153)."""
    return _breakdown_out(account_breakdown(_report(account_id, auth, db, at)))


@router.get("/portfolio/breakdown")
def portfolio_breakdown_route(auth: CurrentAuth, db: DbSession, at: Now) -> BreakdownOut:
    """Allocation and income history of every account in the reporting currency, at the latest
    rates (D-153, §9, §13); an account without a rate is left out and the result is partial."""
    return _breakdown_out(portfolio_breakdown(db, auth.user, at.date()))
