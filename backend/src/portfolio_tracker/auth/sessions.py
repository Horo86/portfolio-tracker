"""Opaque, revocable sessions (ADR-0005): 256-bit random tokens, only SHA-256 stored.

A session ends after `idle` without use or `absolute` after login, whichever comes first
(M2 default: 7 and 30 days; admin-configurable in M6).
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, delete, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from portfolio_tracker.db import BEGIN_OPTION
from portfolio_tracker.db.models import User, UserSession

log = logging.getLogger(__name__)

# last_used_at is refreshed at most this often, so reads rarely write.
TOUCH_INTERVAL = timedelta(minutes=5)
TOUCH_BUSY_TIMEOUT_MS = 200
DEFAULT_BUSY_TIMEOUT_MS = 5000  # pysqlite's default (timeout=5.0)


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
    """The live session and its enabled user, or None. Read-only: it never writes, so a
    read request never has to upgrade its transaction (expired rows are left for a cleanup
    job, M4; they are rejected here anyway)."""
    row = session.scalars(
        select(UserSession).where(UserSession.token_hash == hash_token(token))
    ).one_or_none()
    if row is None:
        return None
    if now >= row.expires_at or now >= row.last_used_at + policy.idle:
        return None
    user = session.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    return row, user


def needs_touch(row: UserSession, now: datetime) -> bool:
    """last_used_at is refreshed at most every TOUCH_INTERVAL, so reads rarely write."""
    return now - row.last_used_at >= TOUCH_INTERVAL


def touch_best_effort(engine: Engine, session_id: UUID, now: datetime) -> None:
    """Refresh last_used_at in its own short write transaction. Used by read requests, whose
    own transaction must stay read-only; if the database is busy the refresh is skipped (the
    next request retries) instead of failing the request."""
    with engine.connect() as connection:
        raw = connection.connection.driver_connection
        raw.execute(f"PRAGMA busy_timeout={TOUCH_BUSY_TIMEOUT_MS}")  # before BEGIN: it waits
        try:
            with connection.execution_options(**{BEGIN_OPTION: "IMMEDIATE"}).begin():
                connection.execute(
                    update(UserSession).where(UserSession.id == session_id).values(last_used_at=now)
                )
        except OperationalError:
            log.info("session last-use refresh skipped: database busy")
        finally:
            raw.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")  # pooled connection


def revoke(session: Session, row: UserSession) -> None:
    session.delete(row)
    session.flush()


def revoke_all(session: Session, user_id: UUID, *, except_id: UUID | None = None) -> int:
    """End every session of a user ("log out everywhere"), optionally keeping one."""
    query = delete(UserSession).where(UserSession.user_id == user_id)
    if except_id is not None:
        query = query.where(UserSession.id != except_id)
    return session.execute(query).rowcount or 0
