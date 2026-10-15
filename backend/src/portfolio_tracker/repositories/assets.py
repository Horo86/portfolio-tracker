"""Assets: private ones (owner set, created by hand, D-37) and shared ones (owner NULL,
provider-resolved, M4).

Visibility (D-35): a user sees their private assets and the shared assets used in their own
accounts. Shared assets a user "added" without using them need a link table, designed with
asset search in M4.
"""

from uuid import UUID

from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Account, Asset, Transaction


def _used_by(owner_id: UUID) -> ColumnElement[bool]:
    return Asset.id.in_(
        select(Transaction.asset_id)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.owner_id == owner_id, Transaction.asset_id.is_not(None))
    )


def _visible_to(owner_id: UUID) -> ColumnElement[bool]:
    return or_(Asset.owner_id == owner_id, (Asset.owner_id.is_(None)) & _used_by(owner_id))


def list_assets(session: Session, owner_id: UUID) -> list[Asset]:
    query = select(Asset).where(_visible_to(owner_id)).order_by(Asset.name, Asset.id)
    return list(session.scalars(query))


def get_asset(session: Session, owner_id: UUID, asset_id: UUID) -> Asset | None:
    """A visible asset (D-35), or None."""
    query = select(Asset).where(Asset.id == asset_id, _visible_to(owner_id))
    return session.scalars(query).one_or_none()


def get_usable_asset(session: Session, owner_id: UUID, asset_id: UUID) -> Asset | None:
    """An asset a transaction of `owner_id` may reference: their private asset or any shared
    one (shared assets are reference data; using one reveals nothing about other users)."""
    query = select(Asset).where(
        Asset.id == asset_id, or_(Asset.owner_id == owner_id, Asset.owner_id.is_(None))
    )
    return session.scalars(query).one_or_none()


def get_private_asset(session: Session, owner_id: UUID, asset_id: UUID) -> Asset | None:
    """A private asset of `owner_id` (the only kind a user may edit or delete)."""
    query = select(Asset).where(Asset.id == asset_id, Asset.owner_id == owner_id)
    return session.scalars(query).one_or_none()


def private_isin_taken(
    session: Session, owner_id: UUID, isin: str, *, except_id: UUID | None = None
) -> bool:
    """True when another private asset of `owner_id` already has this ISIN."""
    query = select(Asset.id).where(Asset.owner_id == owner_id, Asset.isin == isin)
    if except_id is not None:
        query = query.where(Asset.id != except_id)
    return session.scalars(query).first() is not None


def add_private_asset(session: Session, owner_id: UUID, **fields: object) -> Asset:
    asset = Asset(owner_id=owner_id, **fields)
    session.add(asset)
    session.flush()
    return asset


def is_referenced(session: Session, asset: Asset) -> bool:
    """True when any transaction references the asset (then its structural fields and its
    existence are locked, M2 default)."""
    return session.scalar(select(exists().where(Transaction.asset_id == asset.id))) or False


def delete_asset(session: Session, asset: Asset) -> None:
    session.delete(asset)
    session.flush()


def assets_by_id(session: Session, asset_ids: set[UUID]) -> dict[UUID, Asset]:
    """Assets referenced by already owner-checked transactions (internal use only)."""
    if not asset_ids:
        return {}
    return {a.id: a for a in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))}
