"""Daily FX update (D-120, D-122): every currency in use, from its last stored days.

Currencies in use: those of accounts, of asset quote currencies and of users' reporting
currencies (EUR is the base and never fetched). Each currency is fetched from its last
`OVERLAP_DAYS` stored days (fetched again) up to today, or, for a currency fetched for the
first time, from the start of the history horizon (20 years before today, whoever's data
made it needed, D-122), or later when the engine declares a shorter history.

Engines are tried in the configured `FX` order (D-132): a currency the first engine does not
publish, or fails to deliver, is asked of the next one. Each rate records its engine (D-134).

Network discipline (D-121): the plan is read in a short read transaction; the provider is
called with no transaction open; each currency's rates are then written in their own short
write transaction. A failure of one currency never stops the others and stores nothing for
it, so the next run fetches the same range again.
"""

import logging
from collections.abc import Sequence
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
# Every run fetches again the last stored days (D-122): a value stored while its market was
# still open, or corrected later by the provider, is replaced; the upsert is idempotent.
OVERLAP_DAYS = 5


@dataclass
class FxUpdateResult:
    stored: dict[str, int] = field(default_factory=dict)  # rows written per currency
    failed: dict[str, str] = field(default_factory=dict)  # error code per currency


def years_before(today: date, years: int) -> date:
    """The same day `years` earlier (28 Feb when that year has no 29 Feb)."""
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def horizon_start(today: date) -> date:
    """The first day of a new series: 20 years before today (e.g. 2100-02-28 from
    2120-02-29)."""
    return years_before(today, HORIZON_YEARS)


def fetch_start(last_stored: date | None, today: date, history_years: int | None = None) -> date:
    """The first day to fetch: the last `OVERLAP_DAYS` stored days again, then onwards; a
    new series starts at the horizon, or later when the engine offers fewer years."""
    if last_stored is None:
        start = horizon_start(today)
        if history_years is not None:
            start = max(start, years_before(today, history_years))
        return start
    return last_stored - timedelta(days=OVERLAP_DAYS - 1)


def needed_currencies(session: Session) -> set[str]:
    query = union(
        select(Account.currency), select(Asset.quote_currency), select(User.reporting_currency)
    )
    return {code for (code,) in session.execute(query)} - {FX_BASE}


def update_fx(engine: Engine, providers: Sequence[FxProvider], today: date) -> FxUpdateResult:
    """Fetch every currency in use from the engines in `providers` (the `FX` order). A
    currency for which no engine stored anything but one failed reports the first error."""
    with db_transaction(engine, write=False) as session:
        plan = {
            currency: fx.last_stored_day(session, currency)
            for currency in sorted(needed_currencies(session))
        }
    result = FxUpdateResult()
    for currency, last_stored in plan.items():
        for provider in providers:
            start = fetch_start(last_stored, today, provider.capabilities.history_years)
            if start > today:
                break
            try:
                rates = provider.rates([currency], start, today)
            except ProviderError as error:
                result.failed.setdefault(currency, error.code)
                log.warning(
                    "FX update failed",
                    extra={"provider": provider.name, "currency": currency, "code": error.code},
                )
                continue
            rates = [r for r in rates if r.currency == currency]
            if rates:
                with db_transaction(engine, write=True) as session:
                    result.stored[currency] = fx.store_rates(session, rates, provider.name)
                result.failed.pop(currency, None)
                break
    return result
