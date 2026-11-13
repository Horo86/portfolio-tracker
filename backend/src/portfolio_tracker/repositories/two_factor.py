"""Two-factor authentication rows (D-174): each user's TOTP secret and recovery codes.

Always for one user, given by id: there is no query across users except the set of users who
have it on, which the Users screen shows. The secret leaves this module only through
`TwoFactor.secret`, read where a code is checked or the enrolment is shown.
"""

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import RecoveryCode, TwoFactor


def get(session: Session, user_id: UUID) -> TwoFactor | None:
    """The user's row, confirmed or still pending."""
    return session.get(TwoFactor, user_id)


def confirmed(session: Session, user_id: UUID) -> TwoFactor | None:
    """The user's row when two-factor authentication is on, else None."""
    row = get(session, user_id)
    return row if row is not None and row.confirmed_at is not None else None


def is_enabled(session: Session, user_id: UUID) -> bool:
    query = select(TwoFactor.user_id).where(
        TwoFactor.user_id == user_id, TwoFactor.confirmed_at.is_not(None)
    )
    return session.scalar(query) is not None


def enabled_user_ids(session: Session) -> set[UUID]:
    """Who has it on: for the Users screen's column, never with a secret."""
    query = select(TwoFactor.user_id).where(TwoFactor.confirmed_at.is_not(None))
    return set(session.scalars(query))


def begin(session: Session, user_id: UUID, secret: str, at: datetime) -> TwoFactor:
    """Store a new, unconfirmed secret, replacing a pending one. The caller refuses when
    two-factor authentication is already on."""
    session.execute(delete(TwoFactor).where(TwoFactor.user_id == user_id))
    row = TwoFactor(user_id=user_id, secret=secret, created_at=at)
    session.add(row)
    session.flush()
    return row


def delete_stale_pending(session: Session, before: datetime) -> int:
    """Delete the secrets never confirmed and created before `before`: the daily housekeeping
    (D-174). A confirmed secret is never touched. Returns how many."""
    removed = session.execute(
        delete(TwoFactor).where(TwoFactor.confirmed_at.is_(None), TwoFactor.created_at < before)
    )
    return removed.rowcount or 0


def accept_step(session: Session, user_id: UUID, step: int) -> bool:
    """Record `step` as the last accepted one, only if it is newer than the stored one: of two
    requests carrying the same code, only one gets True (the write transaction is `BEGIN
    IMMEDIATE`, and the condition is checked by the database itself)."""
    updated = session.execute(
        update(TwoFactor)
        .where(
            TwoFactor.user_id == user_id,
            (TwoFactor.last_step.is_(None)) | (TwoFactor.last_step < step),
        )
        .values(last_step=step)
    )
    return updated.rowcount == 1


def confirm(session: Session, user_id: UUID, at: datetime) -> None:
    session.execute(update(TwoFactor).where(TwoFactor.user_id == user_id).values(confirmed_at=at))


def replace_recovery_codes(
    session: Session, user_id: UUID, hashes: Iterable[str], at: datetime
) -> None:
    """Drop every code of the user, used or not, and store the new hashes."""
    session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    session.add_all(
        RecoveryCode(user_id=user_id, code_hash=code_hash, created_at=at) for code_hash in hashes
    )
    session.flush()


def unused_recovery_codes(session: Session, user_id: UUID) -> list[tuple[UUID, str]]:
    """The id and hash of each code not used yet, in a stable order."""
    query = (
        select(RecoveryCode.id, RecoveryCode.code_hash)
        .where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        .order_by(RecoveryCode.id)
    )
    return [(row.id, row.code_hash) for row in session.execute(query)]


def recovery_codes_left(session: Session, user_id: UUID) -> int:
    query = select(func.count()).where(
        RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None)
    )
    return session.scalar(query) or 0


def use_recovery_code(session: Session, code_id: UUID, at: datetime) -> bool:
    """Mark a code used, only if it was not: of two requests with one code, one gets True."""
    updated = session.execute(
        update(RecoveryCode)
        .where(RecoveryCode.id == code_id, RecoveryCode.used_at.is_(None))
        .values(used_at=at)
    )
    return updated.rowcount == 1


def remove(session: Session, user_id: UUID) -> bool:
    """Delete the user's secret and recovery codes. Returns whether two-factor
    authentication was on (a pending enrolment is deleted too, and counts as off)."""
    was_on = is_enabled(session, user_id)
    session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))
    session.execute(delete(TwoFactor).where(TwoFactor.user_id == user_id))
    session.flush()
    return was_on
