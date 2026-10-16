"""Users. Not portfolio data: the administrator may list them (no accounts, no figures)."""

from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import User


def any_user_exists(session: Session) -> bool:
    """False only on a fresh installation: the setup wizard is open (D-34)."""
    return session.scalar(select(exists().select_from(User))) or False


def get_user(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_by_username(session: Session, username: str) -> User | None:
    """Case-insensitive (the column collates NOCASE)."""
    return session.scalars(select(User).where(User.username == username)).one_or_none()


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)))


def add_user(session: Session, **fields: object) -> User:
    user = User(**fields)
    session.add(user)
    session.flush()
    return user


def count_active_administrators(session: Session) -> int:
    query = select(func.count()).where(User.role == "ADMINISTRATOR", User.disabled_at.is_(None))
    return session.scalar(query) or 0
