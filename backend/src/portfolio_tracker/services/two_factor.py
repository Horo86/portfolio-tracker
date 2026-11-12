"""Checking a second factor (D-174): a TOTP code or a recovery code, each accepted once.

Runs inside a write transaction (`BEGIN IMMEDIATE`), so the check and the update that makes a
code unusable cannot interleave with another request carrying the same code. Nothing slow
happens here: no password is hashed. Nothing is logged, and no error quotes what was typed.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from portfolio_tracker.auth import recovery, totp
from portfolio_tracker.repositories import two_factor


class Factor(StrEnum):
    TOTP = "totp"
    RECOVERY_CODE = "recovery_code"


def check(
    db: Session, user_id: UUID, at: datetime, *, code: str | None, recovery_code: str | None
) -> Factor | None:
    """Which factor was accepted, or None. Exactly one of `code` and `recovery_code` is
    given. A user without confirmed two-factor authentication accepts nothing."""
    row = two_factor.confirmed(db, user_id)
    if row is None:
        return None
    if code is not None:
        step = totp.matching_step(row.secret, code, at, row.last_step)
        if step is not None and two_factor.accept_step(db, user_id, step):
            return Factor.TOTP
        return None
    if recovery_code is not None:
        unused = two_factor.unused_recovery_codes(db, user_id)
        index = recovery.find(recovery_code, [code_hash for _, code_hash in unused])
        if index is not None and two_factor.use_recovery_code(db, unused[index][0], at):
            return Factor.RECOVERY_CODE
    return None
