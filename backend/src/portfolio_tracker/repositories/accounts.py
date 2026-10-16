"""Investment accounts of one owner."""

from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Account, Transaction


def list_accounts(session: Session, owner_id: UUID) -> list[Account]:
    query = select(Account).where(Account.owner_id == owner_id).order_by(Account.created_at)
    return list(session.scalars(query))


def get_account(session: Session, owner_id: UUID, account_id: UUID) -> Account | None:
    query = select(Account).where(Account.id == account_id, Account.owner_id == owner_id)
    return session.scalars(query).one_or_none()


def add_account(session: Session, owner_id: UUID, **fields: object) -> Account:
    account = Account(owner_id=owner_id, **fields)
    session.add(account)
    session.flush()
    return account


def delete_account(session: Session, account: Account) -> None:
    """Delete an account (already owner-checked); its transactions go with it (D-39)."""
    session.delete(account)
    session.flush()


def has_transactions(session: Session, account: Account) -> bool:
    """The cash currency is locked while this is true (M2 default)."""
    return session.scalar(select(exists().where(Transaction.account_id == account.id))) or False


def count_transactions(session: Session, account: Account) -> int:
    query = select(func.count()).where(Transaction.account_id == account.id)
    return session.scalar(query) or 0
