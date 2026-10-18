"""Daily closes. Manual prices belong to one user and affect only that user (D-35); provider
closes come from the instrument an asset is linked to (D-116), read in the asset's own quote
unit (D-119).

Price of an asset on a day, for its owner (domain-rules §12, D-116): the owner's manual price
on that day if any; otherwise the most recent dated price on or before that day, whatever its
source. Merging both series by day, the manual close winning on its own day, gives exactly
that once the engine carries the last close forward.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import (
    Asset,
    Instrument,
    InstrumentEngineCheck,
    InstrumentListing,
    InstrumentPrice,
    Price,
)
from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.marketdata.units import Mismatch, Rescale, compatible, to_asset_unit


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


@dataclass
class PriceSeries:
    """The closes of one asset as its owner sees them, with where each one comes from."""

    closes: dict[date, Decimal] = field(default_factory=dict)  # merged, asset quote unit
    manual_days: set[date] = field(default_factory=set)
    mismatch: Mismatch | None = None  # the active listing cannot feed this asset (D-119)


def price_series(session: Session, owner_id: UUID, asset_ids: set[UUID]) -> dict[UUID, PriceSeries]:
    """Price series by asset as seen by `owner_id`. Other users' manual prices are never
    read; provider closes come only from the active listing of each asset's instrument, and
    only when that listing fits the asset's currency and unit."""
    result = {asset_id: PriceSeries() for asset_id in asset_ids}
    if not asset_ids:
        return result
    links = (
        select(
            Asset.id,
            Asset.asset_type,
            Asset.quote_currency,
            Asset.quote_unit,
            InstrumentListing.id,
            InstrumentListing.currency,
            InstrumentListing.unit,
        )
        .join(
            Instrument,
            and_(
                Instrument.id == Asset.instrument_id,
                Instrument.isin == Asset.isin,  # a stale link never feeds an asset
                Instrument.quote_currency == Asset.quote_currency,
            ),
        )
        .join(InstrumentListing, InstrumentListing.instrument_id == Instrument.id)
        .where(
            Asset.id.in_(asset_ids),
            Asset.owner_id == owner_id,
            InstrumentListing.is_active,
        )
    )
    readers: dict[UUID, list[tuple[UUID, Rescale]]] = {}  # listing -> assets reading it
    for asset_id, a_type, a_currency, a_unit, listing_id, l_currency, l_unit in session.execute(
        links
    ):
        rule = compatible(
            a_currency, QuoteUnit(a_unit), AssetType(a_type), l_currency, QuoteUnit(l_unit)
        )
        if isinstance(rule, Mismatch):
            result[asset_id].mismatch = rule
        else:
            readers.setdefault(listing_id, []).append((asset_id, rule))
    if readers:
        closes = select(InstrumentPrice.listing_id, InstrumentPrice.day, InstrumentPrice.close)
        for listing_id, day, close in session.execute(
            closes.where(InstrumentPrice.listing_id.in_(readers))
        ):
            for asset_id, rule in readers[listing_id]:
                result[asset_id].closes[day] = to_asset_unit(close, rule)

    manual = select(Price.asset_id, Price.day, Price.close).where(
        Price.asset_id.in_(asset_ids), Price.source == "MANUAL", Price.owner_id == owner_id
    )
    for asset_id, day, close in session.execute(manual):
        result[asset_id].closes[day] = close  # the manual close wins on its own day
        result[asset_id].manual_days.add(day)
    return result


def closes_for(
    session: Session, owner_id: UUID, asset_ids: set[UUID]
) -> dict[UUID, dict[date, Decimal]]:
    """Merged closes by asset and day as seen by `owner_id` (see `price_series`)."""
    if not asset_ids:
        return {}
    return {
        asset_id: series.closes
        for asset_id, series in price_series(session, owner_id, asset_ids).items()
    }


class PriceState(StrEnum):
    """Where an asset's provider prices stand (D-119, D-121, D-134), for its owner."""

    OK = "OK"  # its instrument's active listing feeds it
    MANUAL = "MANUAL"  # no ISIN: manual prices only
    PENDING = "PENDING"  # not linked yet, or its instrument is still being looked up
    NOT_AVAILABLE = "NOT_AVAILABLE"  # no engine has a listing (or none serves its class)
    MISMATCH = "MISMATCH"  # the listing cannot feed this asset, or answers in another unit
    FAILING = "FAILING"  # the listing's last fetch failed: older closes stay in use


@dataclass(frozen=True)
class PriceStatus:
    state: PriceState
    detail: str | None = None  # the mismatch kind (CURRENCY, UNIT, LEVEL)


def price_statuses(
    session: Session, owner_id: UUID, asset_ids: set[UUID]
) -> dict[UUID, PriceStatus]:
    """The price status of each of `owner_id`'s assets. Derived from the shared instrument's
    state only as far as it concerns this asset; nothing tells whether or how many other
    users hold the same ISIN."""
    if not asset_ids:
        return {}
    owned = session.scalars(
        select(Asset).where(Asset.id.in_(asset_ids), Asset.owner_id == owner_id)
    ).all()
    result: dict[UUID, PriceStatus] = {}
    for asset in owned:
        if asset.isin is None:
            result[asset.id] = PriceStatus(PriceState.MANUAL)
            continue
        instrument = session.get(Instrument, asset.instrument_id) if asset.instrument_id else None
        if instrument is None or (instrument.isin, instrument.quote_currency) != (
            asset.isin,
            asset.quote_currency,
        ):
            result[asset.id] = PriceStatus(PriceState.PENDING)  # linked by the next run
            continue
        result[asset.id] = _instrument_status(session, asset, instrument)
    return result


def _instrument_status(session: Session, asset: Asset, instrument: Instrument) -> PriceStatus:
    active = session.scalars(
        select(InstrumentListing).where(
            InstrumentListing.instrument_id == instrument.id, InstrumentListing.is_active
        )
    ).one_or_none()
    if active is None:
        checks = session.scalars(
            select(InstrumentEngineCheck.status).where(
                InstrumentEngineCheck.instrument_id == instrument.id
            )
        ).all()
        if "FAILING" in checks or (not checks and instrument.price_class is None):
            return PriceStatus(PriceState.PENDING)
        return PriceStatus(PriceState.NOT_AVAILABLE)
    rule = compatible(
        asset.quote_currency,
        QuoteUnit(asset.quote_unit),
        AssetType(asset.asset_type),
        active.currency,
        QuoteUnit(active.unit),
    )
    if isinstance(rule, Mismatch):
        return PriceStatus(PriceState.MISMATCH, rule.kind.value)
    if active.status == "MISMATCH":
        return PriceStatus(PriceState.MISMATCH, active.mismatch)
    if active.status in ("FAILING", "NOT_FOUND"):
        return PriceStatus(PriceState.FAILING)
    return PriceStatus(PriceState.OK)
