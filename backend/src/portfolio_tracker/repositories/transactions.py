"""Transactions, always reached through an account of the owner."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Account, Transaction


@dataclass(frozen=True, slots=True)
class TransactionFilter:
    account_id: UUID | None = None
    asset_id: UUID | None = None
    types: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None


def _owned(owner_id: UUID) -> Select[tuple[Transaction]]:
    return (
        select(Transaction)
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.owner_id == owner_id)
    )


def list_transactions(
    session: Session,
    owner_id: UUID,
    flt: TransactionFilter | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Transaction], int]:
    """A page of the owner's transactions, newest first, and the total count."""
    flt = flt or TransactionFilter()
    query = _owned(owner_id)
    if flt.account_id is not None:
        query = query.where(Transaction.account_id == flt.account_id)
    if flt.asset_id is not None:
        query = query.where(Transaction.asset_id == flt.asset_id)
    if flt.types:
        query = query.where(Transaction.type.in_(flt.types))
    if flt.date_from is not None:
        query = query.where(Transaction.trade_date >= flt.date_from)
    if flt.date_to is not None:
        query = query.where(Transaction.trade_date <= flt.date_to)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    page = query.order_by(
        Transaction.trade_date.desc(), Transaction.sequence.desc(), Transaction.id
    )
    return list(session.scalars(page.limit(limit).offset(offset))), total


def get_transaction(session: Session, owner_id: UUID, tx_id: UUID) -> Transaction | None:
    return session.scalars(_owned(owner_id).where(Transaction.id == tx_id)).one_or_none()


def get_transactions(session: Session, owner_id: UUID, tx_ids: set[UUID]) -> list[Transaction]:
    """The owner's transactions among `tx_ids`; ids of other users are simply absent."""
    if not tx_ids:
        return []
    return list(session.scalars(_owned(owner_id).where(Transaction.id.in_(tx_ids))))


def account_ledger(session: Session, owner_id: UUID, account_id: UUID) -> list[Transaction]:
    """Every transaction of one account of the owner (the input of a replay)."""
    query = _owned(owner_id).where(Transaction.account_id == account_id)
    return list(session.scalars(query.order_by(Transaction.sequence)))


def next_sequence(session: Session, account_id: UUID) -> int:
    """Next insertion sequence of an account (D-70): one more than the largest current one,
    so a new transaction always sorts after the existing ones on ties. Call only under the
    write lock (BEGIN IMMEDIATE)."""
    query = select(func.max(Transaction.sequence)).where(Transaction.account_id == account_id)
    return (session.scalar(query) or 0) + 1


def delete_transactions(session: Session, rows: list[Transaction]) -> None:
    """Delete already owner-checked rows."""
    if rows:
        session.execute(delete(Transaction).where(Transaction.id.in_([r.id for r in rows])))
        session.flush()


def last_trade_date(session: Session, owner_id: UUID) -> date | None:
    """Date of the owner's latest transaction over all their accounts."""
    query = (
        select(func.max(Transaction.trade_date))
        .join(Account, Account.id == Transaction.account_id)
        .where(Account.owner_id == owner_id)
    )
    return session.scalar(query)
