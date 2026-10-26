"""Assets and manual prices (D-35, D-37, D-44, D-67; mockups 11 and 12).

A user sees their private assets and the shared assets used in their accounts; only private
assets are editable. Manual prices belong to the user who entered them and affect only that
user's figures. Every asset is private since D-116; the search asks the provider live
(D-128, `services/asset_search.py`).
"""

from datetime import date, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Request, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import Auth, CurrentAuth, DbSession, Now, ShortAuth
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import Currency, DecimalStr, Isin, Strict
from portfolio_tracker.db.models import Asset, Price
from portfolio_tracker.engine.model import AssetType, QuoteUnit
from portfolio_tracker.engine.validation import validate_asset
from portfolio_tracker.repositories import assets, audit, prices
from portfolio_tracker.repositories.mapping import to_engine_asset
from portfolio_tracker.services import asset_search
from portfolio_tracker.services.market_startup import queue_linking

router = APIRouter(prefix="/assets", tags=["assets"])

AssetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Ticker = Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)]

# Fields that change how transactions of the asset are validated and valued: locked while
# any transaction references the asset (M2 default).
STRUCTURAL = ("asset_type", "quote_currency", "quote_unit", "isin")


class AssetOut(BaseModel):
    id: UUID
    isin: str | None
    ticker: str | None
    name: str
    asset_type: AssetType
    quote_currency: str
    quote_unit: QuoteUnit
    shared: bool  # provider-resolved reference data (M4); read-only for users
    in_use: bool  # referenced by a transaction: type, currency, unit and ISIN are locked
    created_at: datetime


class AssetIn(Strict):
    name: AssetName
    asset_type: AssetType
    quote_currency: Currency
    quote_unit: QuoteUnit
    isin: Isin | None = None
    ticker: Ticker | None = None


class AssetPatch(Strict):
    name: AssetName | None = None
    asset_type: AssetType | None = None
    quote_currency: Currency | None = None
    quote_unit: QuoteUnit | None = None
    isin: Isin | None = None
    ticker: Ticker | None = None


class PriceIn(Strict):
    day: date
    close: DecimalStr  # in the asset quote unit (currency, minor unit or % of nominal)


class SearchIn(Strict):
    # In the body, not the URL: the text stays out of access logs and proxy logs.
    q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)]


class SearchResultOut(BaseModel):
    """One provider result. To add it, send its `isin` and `currency` (with a name, type and
    unit) to `POST /assets`: the listing that prices the asset is chosen by the linking job,
    never by this result (D-125). `isin` is null for a text search."""

    name: str | None
    symbol: str  # the provider's, for display
    exchange: str | None
    currency: str
    quote_unit: QuoteUnit
    asset_type: AssetType | None  # the provider's proposal
    isin: str | None


class PriceOut(BaseModel):
    id: UUID
    day: date
    close: DecimalStr
    source: str


def asset_out(db: Session, asset: Asset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        isin=asset.isin,
        ticker=asset.ticker,
        name=asset.name,
        asset_type=asset.asset_type,
        quote_currency=asset.quote_currency,
        quote_unit=asset.quote_unit,
        shared=asset.owner_id is None,
        in_use=assets.is_referenced(db, asset),
        created_at=asset.created_at,
    )


def _fields(asset: Asset) -> dict:
    return {name: getattr(asset, name) for name in ("name", "ticker", *STRUCTURAL)}


def _check_asset_rules(asset: Asset) -> None:
    issues = validate_asset(to_engine_asset(asset))
    if issues:
        raise ApiError(
            422,
            "VALIDATION_FAILED",
            "the asset breaks a field rule",
            issues=[{"field": i.field, "code": i.code} for i in issues],
        )


def _check_isin_free(db: Session, auth: Auth, isin: str | None, except_id: UUID | None = None):
    """A user cannot have two private assets with the same ISIN (M2 decision); the ISIN
    stays optional."""
    if isin and assets.private_isin_taken(db, auth.user.id, isin, except_id=except_id):
        raise ApiError(409, "ISIN_ALREADY_USED", "you already have an asset with this ISIN")


def visible_asset(db: Session, auth: Auth, asset_id: UUID) -> Asset:
    asset = assets.get_asset(db, auth.user.id, asset_id)
    if asset is None:
        raise not_found("asset")
    return asset


def editable_asset(db: Session, auth: Auth, asset_id: UUID) -> Asset:
    """The user's private asset; a visible shared asset is read-only (403), anything else 404."""
    asset = assets.get_private_asset(db, auth.user.id, asset_id)
    if asset is None:
        visible_asset(db, auth, asset_id)
        raise ApiError(403, "ASSET_READ_ONLY", "shared assets cannot be changed")
    return asset


@router.post(
    "/search",
    responses={
        429: {"description": "TOO_MANY_ATTEMPTS (with Retry-After)"},
        503: {"description": "MARKET_DATA_OFF, MARKET_DATA_BUSY (with Retry-After) or "
                             "MARKET_DATA_UNAVAILABLE"},
    },
)  # fmt: skip
def search_assets(
    body: SearchIn, auth: ShortAuth, request: Request, at: Now
) -> list[SearchResultOut]:
    """Search the provider by ISIN or by ticker or name (D-128): its results only, never
    anything stored, so nothing tells what other users hold. No database transaction is open
    while the provider answers."""
    market_data = request.app.state.market_data
    if not market_data.on:
        raise ApiError(503, "MARKET_DATA_OFF", "market data is off on this instance")
    wait = request.app.state.search_limiter.take(auth.user.id, at)
    if wait is not None:
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "too many searches", retry_after=wait)
    engines, exchanges = market_data.engines(request.app.state.engine)
    try:
        found = asset_search.search(engines, exchanges, body.q)
    except asset_search.SearchUnavailable as error:
        extra = {"retry_after": error.retry_after} if error.retry_after else {}
        raise ApiError(503, error.code, "the price provider cannot answer now", **extra) from None
    return [SearchResultOut.model_validate(r, from_attributes=True) for r in found]


@router.get("")
def list_assets(auth: CurrentAuth, db: DbSession) -> list[AssetOut]:
    return [asset_out(db, a) for a in assets.list_assets(db, auth.user.id)]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_asset(
    body: AssetIn,
    auth: CurrentAuth,
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> AssetOut:
    """An asset created by hand, or from a search result, is private to its creator (D-37,
    D-116). Only its ISIN and quote currency tie it to a provider series: the listing is
    chosen by the linking job queued after the response (D-125, D-136), never by the user."""
    fields = body.model_dump()
    fields["ticker"] = fields["ticker"] or None
    asset = Asset(owner_id=auth.user.id, **fields)
    _check_asset_rules(asset)
    _check_isin_free(db, auth, body.isin)
    asset = assets.add_private_asset(db, auth.user.id, **fields)
    audit.record(
        db, "asset.created", actor_id=auth.user.id, entity_type="asset", entity_id=asset.id,
        after=_fields(asset), ip=client_ip(request),
    )  # fmt: skip
    if asset.isin:
        background.add_task(queue_linking, request.app.state.market_data)
    return asset_out(db, asset)


@router.get("/{asset_id}")
def get_asset(asset_id: UUID, auth: CurrentAuth, db: DbSession) -> AssetOut:
    return asset_out(db, visible_asset(db, auth, asset_id))


@router.patch("/{asset_id}")
def update_asset(
    asset_id: UUID,
    body: AssetPatch,
    auth: CurrentAuth,
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> AssetOut:
    """Name and ticker are always editable; type, quote currency, quote unit and ISIN only
    while no transaction references the asset."""
    asset = editable_asset(db, auth, asset_id)
    changes = body.model_dump(exclude_unset=True)
    for name in ("name", "asset_type", "quote_currency", "quote_unit"):
        if name in changes and changes[name] is None:
            raise ApiError(422, "REQUEST_INVALID", f"{name} cannot be null")
    if "ticker" in changes:
        changes["ticker"] = changes["ticker"] or None
    structural = {k for k in STRUCTURAL if k in changes and changes[k] != getattr(asset, k)}
    if structural and assets.is_referenced(db, asset):
        raise ApiError(
            409,
            "ASSET_IN_USE",
            "type, quote currency, quote unit and ISIN cannot change while transactions use it",
            fields=sorted(structural),
        )
    if changes.get("isin"):
        _check_isin_free(db, auth, changes["isin"], except_id=asset.id)
    before = _fields(asset)
    relink = any(
        name in changes and changes[name] != getattr(asset, name)
        for name in ("isin", "quote_currency")
    )
    for name, value in changes.items():
        setattr(asset, name, value)
    if relink:  # another ISIN or currency is another instrument (D-116, D-125)
        asset.instrument_id = None
        if asset.isin:
            background.add_task(queue_linking, request.app.state.market_data)
    _check_asset_rules(asset)  # raising rolls the change back
    db.flush()
    audit.record(
        db, "asset.updated", actor_id=auth.user.id, entity_type="asset", entity_id=asset.id,
        before=before, after=_fields(asset), ip=client_ip(request),
    )  # fmt: skip
    return asset_out(db, asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: UUID, auth: CurrentAuth, request: Request, db: DbSession) -> None:
    """Only an asset no transaction uses; its manual prices go with it."""
    asset = editable_asset(db, auth, asset_id)
    if assets.is_referenced(db, asset):
        raise ApiError(409, "ASSET_IN_USE", "the asset is used by transactions")
    before = _fields(asset)
    assets.delete_asset(db, asset)
    audit.record(
        db, "asset.deleted", actor_id=auth.user.id, entity_type="asset", entity_id=asset_id,
        before=before, ip=client_ip(request),
    )  # fmt: skip


@router.get("/{asset_id}/prices")
def list_manual_prices(asset_id: UUID, auth: CurrentAuth, db: DbSession) -> list[PriceOut]:
    """The user's own manual prices of a visible asset, newest first."""
    asset = visible_asset(db, auth, asset_id)
    return [_price_out(p) for p in prices.list_manual_prices(db, auth.user.id, asset.id)]


@router.put("/{asset_id}/prices")
def set_manual_price(
    asset_id: UUID, body: PriceIn, auth: CurrentAuth, request: Request, db: DbSession, at: Now
) -> PriceOut:
    """Set the user's close for a day (replaces an earlier manual one for that day). The day
    may be at most tomorrow by the server's UTC date, for a user ahead of its time zone (D-149)."""
    asset = visible_asset(db, auth, asset_id)
    if body.close < 0:
        raise ApiError(422, "VALIDATION_FAILED", "a price cannot be negative",
                       issues=[{"field": "close", "code": "must_not_be_negative"}])  # fmt: skip
    if body.day > at.date() + timedelta(days=1):
        raise ApiError(422, "VALIDATION_FAILED", "a price cannot be dated after tomorrow",
                       issues=[{"field": "day", "code": "future_date"}])  # fmt: skip
    old = prices.manual_price_on(db, auth.user.id, asset.id, body.day)
    before = _price_fields(old) if old else None
    price = prices.upsert_manual_price(db, auth.user.id, asset.id, body.day, body.close)
    audit.record(
        db, "price.set", actor_id=auth.user.id, entity_type="price", entity_id=price.id,
        before=before, after=_price_fields(price), ip=client_ip(request),
    )  # fmt: skip
    return _price_out(price)


@router.delete("/{asset_id}/prices/{price_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_manual_price(
    asset_id: UUID, price_id: UUID, auth: CurrentAuth, request: Request, db: DbSession
) -> None:
    visible_asset(db, auth, asset_id)
    price = prices.get_manual_price(db, auth.user.id, price_id)
    if price is None or price.asset_id != asset_id:
        raise not_found("price")
    before = _price_fields(price)
    prices.delete_price(db, price)
    audit.record(
        db, "price.deleted", actor_id=auth.user.id, entity_type="price", entity_id=price_id,
        before=before, ip=client_ip(request),
    )  # fmt: skip


def _price_fields(price: Price) -> dict:
    return {"asset_id": price.asset_id, "day": price.day, "close": price.close}


def _price_out(price: Price) -> PriceOut:
    return PriceOut(id=price.id, day=price.day, close=price.close, source=price.source)
