"""Opaque, revocable sessions (ADR-0005): 256-bit random tokens, only SHA-256 stored.

A session ends after `idle` without use or `absolute` after login, whichever comes first
(M2 default: 7 and 30 days; admin-configurable in M6).
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import User, UserSession

# last_used_at is refreshed at most this often, so reads rarely write.
TOUCH_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    idle: timedelta
    absolute: timedelta


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(
    session: Session, user: User, now: datetime, policy: SessionPolicy, user_agent: str | None
) -> tuple[str, UserSession]:
    """Returns the token (given to the client once) and the stored row."""
    token = new_token()
    row = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        created_at=now,
        last_used_at=now,
        expires_at=now + policy.absolute,
        user_agent=(user_agent or "")[:300] or None,
    )
    session.add(row)
    session.flush()
    return token, row


def resolve(
    session: Session, token: str, now: datetime, policy: SessionPolicy
) -> tuple[UserSession, User] | None:
    """The live session and its enabled user, or None. Expired sessions are deleted."""
    row = session.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    ).one_or_none()
    if row is None:
        return None
    if now >= row.expires_at or now >= row.last_used_at + policy.idle:
        session.delete(row)
        session.flush()
        return None
    user = session.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    if now - row.last_used_at >= TOUCH_INTERVAL:
        row.last_used_at = now
        session.flush()
    return row, user


def revoke(session: Session, row: UserSession) -> None:
    session.delete(row)
    session.flush()


def revoke_all(session: Session, user_id: UUID, *, except_id: UUID | None = None) -> int:
    """End every session of a user ("log out everywhere"), optionally keeping one."""
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if except_id is not None:
        query = query.where(UserSession.id != except_id)
    return session.execute(query).rowcount or 0
