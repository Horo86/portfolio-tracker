"""Daily closes. Provider prices are shared; manual prices belong to one user and, for that
user only, override a provider price on the same day (D-35, domain-model.md)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Price


def list_manual_prices(session: Session, owner_id: UUID, asset_id: UUID) -> list[Price]:
    query = (
        select(Price)
        .where(Price.asset_id == asset_id, Price.source == "MANUAL", Price.owner_id == owner_id)
        .order_by(Price.day.desc())
    )
    return list(session.scalars(query))


def get_manual_price(session: Session, owner_id: UUID, price_id: UUID) -> Price | None:
    query = select(Price).where(
        Price.id == price_id, Price.source == "MANUAL", Price.owner_id == owner_id
    )
    return session.scalars(query).one_or_none()


def manual_price_on(session: Session, owner_id: UUID, asset_id: UUID, day: date) -> Price | None:
    query = select(Price).where(
        Price.asset_id == asset_id,
        Price.day == day,
        Price.source == "MANUAL",
        Price.owner_id == owner_id,
    )
    return session.scalars(query).one_or_none()


def upsert_manual_price(
    session: Session, owner_id: UUID, asset_id: UUID, day: date, close: Decimal
) -> Price:
    """Set the owner's manual close for a day, replacing an earlier one for the same day."""
    price = manual_price_on(session, owner_id, asset_id, day)
    if price is None:
        price = Price(asset_id=asset_id, day=day, close=close, source="MANUAL", owner_id=owner_id)
        session.add(price)
    else:
        price.close = close
    session.flush()
    return price


def delete_price(session: Session, price: Price) -> None:
    session.delete(price)
    session.flush()


def closes_for(
    session: Session, owner_id: UUID, asset_ids: set[UUID]
) -> dict[UUID, dict[date, Decimal]]:
    """Closes by asset and day as seen by `owner_id`: provider prices merged with the owner's
    manual prices, the manual one winning on the same day. Other users' manual prices are
    never read."""
    if not asset_ids:
        return {}
    query = select(Price).where(
        Price.asset_id.in_(asset_ids),
        or_(Price.source == "PROVIDER", Price.owner_id == owner_id),
    )
    result: dict[UUID, dict[date, Decimal]] = {asset_id: {} for asset_id in asset_ids}
    for price in session.scalars(query):
        if price.source == "MANUAL":
            result[price.asset_id][price.day] = price.close
        else:  # never replaces a manual close, whatever the row order
            result[price.asset_id].setdefault(price.day, price.close)
    return result
