"""Users. Not portfolio data: the administrator may list them (no accounts, no figures)."""

from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import User
from portfolio_tracker.identity import username_key


def any_user_exists(session: Session) -> bool:
    """False only on a fresh installation: the setup wizard is open (D-34)."""
    return session.scalar(select(exists().select_from(User))) or False


def get_user(session: Session, user_id: UUID) -> User | None:
    return session.get(User, user_id)


def get_by_username(session: Session, username: str) -> User | None:
    """Through the case-folded key (D-173): `Àlex` finds `àlex`, whatever the alphabet."""
    query = select(User).where(User.username_key == username_key(username))
    return session.scalars(query).one_or_none()


def get_by_email(session: Session, email: str) -> User | None:
    """`email` as stored: trimmed and lowercased (`identity.normalize_email`)."""
    return session.scalars(select(User).where(User.email == email)).one_or_none()


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username_key)))


def add_user(session: Session, *, username: str, **fields: object) -> User:
    """`username` normalised as the API does it; its key is set here (D-173)."""
    user = User(username=username, username_key=username_key(username), **fields)
    session.add(user)
    session.flush()
    return user


def count_active_administrators(session: Session) -> int:
    query = select(func.count()).where(User.role == "ADMINISTRATOR", User.disabled_at.is_(None))
    return session.scalar(query) or 0
