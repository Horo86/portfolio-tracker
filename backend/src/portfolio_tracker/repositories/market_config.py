"""The stored engine configuration (D-132): on/off switches, the order per role, the
preferred exchanges, and a pending listing re-check request.

Instance-wide settings: no owner. Read by the market data service; written when seeding from
the environment, by the reset command (step 10) and by the admin API (M6).
"""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import MarketDataConfig, ProviderRoute, ProviderSetting
from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.routing import RoutingConfig


def _now() -> datetime:
    return datetime.now(UTC)


def load(session: Session) -> RoutingConfig | None:
    """The stored configuration; None while nothing has been seeded."""
    row = session.get(MarketDataConfig, 1)
    if row is None:
        return None
    enabled = session.scalars(select(ProviderSetting.name).where(ProviderSetting.enabled))
    orders: dict[Role, list[str]] = {role: [] for role in Role}
    routes = session.scalars(select(ProviderRoute).order_by(ProviderRoute.position))
    for route in routes:
        orders[Role(route.role)].append(route.provider)
    return RoutingConfig(
        frozenset(enabled),
        {role: tuple(names) for role, names in orders.items()},
        tuple(row.exchanges),
    )


def save(session: Session, config: RoutingConfig) -> None:
    """Replace the stored configuration with `config`. A pending re-check request is kept."""
    session.execute(delete(ProviderRoute))
    session.execute(delete(ProviderSetting))
    session.flush()
    for name in config.names():
        session.add(ProviderSetting(name=name, enabled=name in config.enabled))
    for role in Role:
        for position, name in enumerate(config.order(role)):
            session.add(ProviderRoute(role=role.value, position=position, provider=name))
    row = session.get(MarketDataConfig, 1)
    if row is None:
        session.add(MarketDataConfig(id=1, exchanges=list(config.exchanges)))
    else:
        row.exchanges = list(config.exchanges)
        row.updated_at = _now()
    session.flush()


def seed_if_empty(session: Session, config: RoutingConfig) -> bool:
    """Store `config` (the environment's initial values) only while nothing is stored."""
    if session.get(MarketDataConfig, 1) is not None:
        return False
    save(session, config)
    return True


def request_resolve(session: Session, at: datetime | None = None) -> None:
    """Ask the app's next run for a listing re-check (`RESOLVE`, D-134). The latest request
    is kept, so a request made while a re-check runs is not lost (`clear_resolve_request`)."""
    row = session.get(MarketDataConfig, 1)
    if row is None:
        raise LookupError("the market data configuration has not been seeded")
    row.resolve_requested_at = at or _now()
    session.flush()


def resolve_requested_at(session: Session) -> datetime | None:
    row = session.get(MarketDataConfig, 1)
    return row.resolve_requested_at if row else None


def clear_resolve_request(session: Session, handled: datetime) -> None:
    """Clear the request once a re-check that started at `handled` is done; a request made
    after that start stays pending."""
    session.execute(
        update(MarketDataConfig)
        .where(MarketDataConfig.resolve_requested_at <= handled)
        .values(resolve_requested_at=None)
    )
