"""Stored FX reference rates: units of a currency per 1 EUR, by day (D-120, D-122).

Rates are public reference data, shared by every user; nothing here depends on an owner.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import FxRate
from portfolio_tracker.marketdata.base import Rate

_CHUNK = 500


def store_rates(session: Session, rates: Iterable[Rate], source: str) -> int:
    """Insert rates, replacing a stored rate of the same currency and day. Returns the
    number of rows written."""
    rows = [
        {"currency": r.currency, "day": r.day, "rate": r.per_eur, "source": source} for r in rates
    ]
    for start in range(0, len(rows), _CHUNK):
        statement = insert(FxRate).values(rows[start : start + _CHUNK])
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[FxRate.currency, FxRate.day],
                set_={"rate": statement.excluded.rate, "source": statement.excluded.source},
            )
        )
    return len(rows)


def last_stored_day(session: Session, currency: str) -> date | None:
    return session.scalar(select(func.max(FxRate.day)).where(FxRate.currency == currency))


def overlap_start(session: Session, currency: str, days: int) -> date | None:
    """The first of a currency's last `days` stored days (all of them when fewer): where a run
    starts fetching again (D-122). None when nothing is stored."""
    recent = (
        select(FxRate.day)
        .where(FxRate.currency == currency)
        .order_by(FxRate.day.desc())
        .limit(days)
        .subquery()
    )
    return session.scalar(select(func.min(recent.c.day)))


def latest_rates(
    session: Session, currencies: set[str], day: date
) -> dict[str, dict[date, Decimal]]:
    """For each of `currencies`, its latest stored rate on or before `day` (an empty series
    when there is none): all that the current figures need (§9)."""
    result: dict[str, dict[date, Decimal]] = {}
    for currency in sorted(currencies):
        latest = (
            select(FxRate.day, FxRate.rate)
            .where(FxRate.currency == currency, FxRate.day <= day)
            .order_by(FxRate.day.desc())
            .limit(1)
        )
        result[currency] = {found.day: found.rate for found in session.execute(latest)}
    return result


def rates_for(session: Session, currencies: set[str]) -> dict[str, dict[date, Decimal]]:
    """Every stored rate of `currencies` (the engine carries the last one forward)."""
    result: dict[str, dict[date, Decimal]] = {currency: {} for currency in currencies}
    if not currencies:
        return result
    query = select(FxRate.currency, FxRate.day, FxRate.rate).where(FxRate.currency.in_(currencies))
    for currency, day, rate in session.execute(query):
        result[currency][day] = rate
    return result
