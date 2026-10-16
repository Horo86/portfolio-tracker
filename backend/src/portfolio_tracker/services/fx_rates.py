"""Daily FX update (D-120, D-122): every currency in use, from its last stored day.

Currencies in use: those of accounts, of asset quote currencies and of users' reporting
currencies (EUR is the base and never fetched). Each currency is fetched from the day after
its last stored rate, or, for a currency fetched for the first time, from the start of the
history horizon (20 years before today, whoever's data made it needed, D-122), up to today.

Network discipline (D-121): the plan is read in a short read transaction; the provider is
called with no transaction open; each currency's rates are then written in their own short
write transaction. A failure of one currency never stops the others and stores nothing for
it, so the next run fetches the same range again.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import Engine, select, union
from sqlalchemy.orm import Session

from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import Account, Asset, User
from portfolio_tracker.marketdata.base import FxProvider, ProviderError
from portfolio_tracker.repositories import fx

log = logging.getLogger(__name__)

FX_BASE = "EUR"
HORIZON_YEARS = 20  # requirements.md reference size: 20 years of daily prices


@dataclass
class FxUpdateResult:
    stored: dict[str, int] = field(default_factory=dict)  # rows written per currency
    failed: dict[str, str] = field(default_factory=dict)  # error code per currency


def horizon_start(today: date) -> date:
    """The first day of a new series: 20 years before today (28 Feb when that year has no
    29 Feb, e.g. from 2120)."""
    try:
        return today.replace(year=today.year - HORIZON_YEARS)
    except ValueError:
        return today.replace(year=today.year - HORIZON_YEARS, day=28)


def fetch_start(last_stored: date | None, today: date) -> date:
    return last_stored + timedelta(days=1) if last_stored else horizon_start(today)


def needed_currencies(session: Session) -> set[str]:
    query = union(
        select(Account.currency), select(Asset.quote_currency), select(User.reporting_currency)
    )
    return {code for (code,) in session.execute(query)} - {FX_BASE}


def update_fx(engine: Engine, provider: FxProvider, today: date) -> FxUpdateResult:
    with db_transaction(engine, write=False) as session:
        plan = {
            currency: fetch_start(fx.last_stored_day(session, currency), today)
            for currency in sorted(needed_currencies(session))
        }
    result = FxUpdateResult()
    for currency, start in plan.items():
        if start > today:
            continue
        try:
            rates = [r for r in provider.rates([currency], start, today) if r.currency == currency]
        except ProviderError as error:
            result.failed[currency] = error.code
            log.warning(
                "FX update failed",
                extra={"provider": provider.name, "currency": currency, "code": error.code},
            )
            continue
        if rates:
            with db_transaction(engine, write=True) as session:
                result.stored[currency] = fx.store_rates(session, rates, provider.name)
    return result
