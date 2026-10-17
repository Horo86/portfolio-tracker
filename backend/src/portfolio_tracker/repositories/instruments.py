"""Instruments, their provider listings and closes (D-116, D-119, D-122, D-125, D-133,
D-134).

**Internal only.** These functions take no owner and are called by the market data service,
never with ids from a request: no route lists or returns an instrument or a listing, and
nothing here tells which or how many users are linked to one.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Asset, Instrument, InstrumentListing, InstrumentPrice
from portfolio_tracker.marketdata.base import PriceClass

_CHUNK = 500  # rows per INSERT: far below SQLite's limit on bound parameters
# `class_source` of a price class inferred from the unit of the first accepted listing
# (D-133); engine names are lower case, so it never collides with one.
CLASS_FROM_UNIT = "UNIT"


@dataclass(frozen=True, slots=True)
class StoredClose:
    """A close as stored: already in the major unit (`marketdata.units.to_stored`)."""

    day: date
    close: Decimal
    meta: dict[str, Any] | None = None


def get_instrument(session: Session, isin: str, quote_currency: str) -> Instrument | None:
    query = select(Instrument).where(
        Instrument.isin == isin, Instrument.quote_currency == quote_currency
    )
    return session.scalars(query).one_or_none()


def get_or_create_instrument(session: Session, isin: str, quote_currency: str) -> Instrument:
    instrument = get_instrument(session, isin, quote_currency)
    if instrument is None:
        instrument = Instrument(isin=isin, quote_currency=quote_currency)
        session.add(instrument)
        session.flush()
    return instrument


def set_price_class(
    session: Session, instrument: Instrument, price_class: PriceClass, source: str
) -> None:
    """Record the instrument's price class and where it came from: an engine's name, or
    `CLASS_FROM_UNIT`."""
    instrument.price_class = price_class.value
    instrument.class_source = source
    session.flush()


def link_asset(session: Session, asset_id: UUID, instrument_id: UUID | None) -> None:
    session.execute(update(Asset).where(Asset.id == asset_id).values(instrument_id=instrument_id))


def assets_to_link(session: Session) -> list[Asset]:
    """Assets with an ISIN that are not linked to an instrument yet (any owner)."""
    query = (
        select(Asset)
        .where(Asset.isin.is_not(None), Asset.instrument_id.is_(None))
        .order_by(Asset.created_at, Asset.id)
    )
    return list(session.scalars(query))


def linked_instruments(session: Session) -> list[Instrument]:
    """Instruments still needed: linked to at least one asset (D-122)."""
    linked = select(Asset.instrument_id).where(Asset.instrument_id.is_not(None))
    query = select(Instrument).where(Instrument.id.in_(linked)).order_by(Instrument.created_at)
    return list(session.scalars(query))


def listings(session: Session, instrument_id: UUID) -> list[InstrumentListing]:
    query = (
        select(InstrumentListing)
        .where(InstrumentListing.instrument_id == instrument_id)
        .order_by(InstrumentListing.provider)
    )
    return list(session.scalars(query))


def get_listing(session: Session, instrument_id: UUID, provider: str) -> InstrumentListing | None:
    query = select(InstrumentListing).where(
        InstrumentListing.instrument_id == instrument_id, InstrumentListing.provider == provider
    )
    return session.scalars(query).one_or_none()


def active_listing(session: Session, instrument_id: UUID) -> InstrumentListing | None:
    query = select(InstrumentListing).where(
        InstrumentListing.instrument_id == instrument_id, InstrumentListing.is_active
    )
    return session.scalars(query).one_or_none()


def save_listing(
    session: Session, instrument_id: UUID, provider: str, **fields: object
) -> InstrumentListing:
    """Create or update the listing of `provider` for an instrument."""
    listing = get_listing(session, instrument_id, provider)
    if listing is None:
        listing = InstrumentListing(instrument_id=instrument_id, provider=provider, **fields)
        session.add(listing)
    else:
        for name, value in fields.items():
            setattr(listing, name, value)
    session.flush()
    return listing


def activate(session: Session, listing: InstrumentListing, at: datetime | None = None) -> None:
    """Make `listing` the one whose closes the linked assets read; the others stop. The
    switch is dated on the listing (D-134)."""
    session.execute(
        update(InstrumentListing)
        .where(
            InstrumentListing.instrument_id == listing.instrument_id,
            InstrumentListing.id != listing.id,
        )
        .values(is_active=False)
    )
    listing.is_active = True
    listing.activated_at = at or datetime.now(UTC)
    session.flush()


def store_closes(session: Session, listing_id: UUID, closes: Iterable[StoredClose]) -> int:
    """Insert closes, replacing a stored close of the same day (a provider may correct one).
    Returns the number of rows written."""
    rows = [
        {"listing_id": listing_id, "day": c.day, "close": c.close, "meta": c.meta} for c in closes
    ]
    for start in range(0, len(rows), _CHUNK):
        statement = insert(InstrumentPrice).values(rows[start : start + _CHUNK])
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[InstrumentPrice.listing_id, InstrumentPrice.day],
                set_={
                    "close": statement.excluded.close,
                    "metadata": statement.excluded["metadata"],
                },
            )
        )
    return len(rows)


def last_stored_day(session: Session, listing_id: UUID) -> date | None:
    return session.scalar(
        select(func.max(InstrumentPrice.day)).where(InstrumentPrice.listing_id == listing_id)
    )


def stored_closes(session: Session, listing_id: UUID) -> Sequence[InstrumentPrice]:
    query = (
        select(InstrumentPrice)
        .where(InstrumentPrice.listing_id == listing_id)
        .order_by(InstrumentPrice.day)
    )
    return list(session.scalars(query))


def listing_counts(session: Session) -> dict[tuple[str, str], int]:
    """Listings per (engine, status): counts only, for the administrator's status (D-135)."""
    query = select(InstrumentListing.provider, InstrumentListing.status, func.count()).group_by(
        InstrumentListing.provider, InstrumentListing.status
    )
    return {(provider, status): count for provider, status, count in session.execute(query)}
