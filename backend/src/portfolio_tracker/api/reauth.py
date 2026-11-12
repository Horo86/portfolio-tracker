"""Asking again for what the user knows before a sensitive change: the current password, and
a second factor (D-82, D-174).

Each check is throttled per user and audited, so a stolen session cannot be used to find the
password or a code by trying. A wrong answer is a 422 with its own code, never a 401: the
session is still valid, and the page must not think it ended.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import transaction
from portfolio_tracker.api.errors import error_response
from portfolio_tracker.auth.passwords import PasswordChecksBusy
from portfolio_tracker.auth.throttle import password_change_keys, totp_keys
from portfolio_tracker.db.models import User
from portfolio_tracker.repositories import audit, two_factor
from portfolio_tracker.services.two_factor import check

log = logging.getLogger(__name__)


class _WrongCode(Exception):
    """Inside `with_code` only: the code was wrong, undo the transaction."""


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def wrong_password(
    request: Request,
    user: User,
    password: str,
    at: datetime,
    *,
    what: str = "password change",
    action: str = "user.password_change_failed",
) -> JSONResponse | None:
    """None when `password` is the user's current one, else the answer to send: 422
    `CURRENT_PASSWORD_WRONG`, or 429 `TOO_MANY_ATTEMPTS` while the user is blocked.

    The Argon2id check takes a slot (D-162) and runs outside any write transaction (D-94):
    call it before opening one. `what` names the operation in the log, `action` in the audit
    log."""
    app = request.app
    ip = _client_ip(request)
    with app.state.login_throttle.attempt(password_change_keys(user.id), at) as attempt:
        if attempt.refused:
            if attempt.first_refusal:
                log.warning(f"{what} blocked", extra={"user_id": str(user.id), "ip": ip})
            return error_response(
                429,
                "TOO_MANY_ATTEMPTS",
                "too many wrong passwords",
                retry_after=attempt.retry_after,
            )
        try:
            verified = app.state.passwords.verify(user.password_hash, password)
        except PasswordChecksBusy:
            attempt.void()  # no slot: not a guess (D-162)
            raise
        if verified:
            attempt.success()  # the current password is proven; what follows is not a guess
            return None
    log.warning(f"{what}: wrong current password", extra={"user_id": str(user.id)})
    with transaction(app, write=True) as db:
        audit.record(db, action, actor_id=user.id, entity_type="user", entity_id=user.id, ip=ip)
    return error_response(422, "CURRENT_PASSWORD_WRONG", "the current password is wrong")


def with_code[T](
    request: Request, user: User, at: datetime, work: Callable[[Session], T | None]
) -> T | JSONResponse:
    """Run `work` in a write transaction as one guess at a TOTP or recovery code of `user`.

    `work` checks the code itself (so the check and what follows are one transaction) and
    returns its result, or None when the code was wrong: then the transaction is rolled back,
    so nothing `work` did is kept, the guess is counted under the user's `totp` key and
    audited, and the answer is 422 `TWO_FACTOR_CODE_WRONG`. An `ApiError` raised by `work` is
    not a guess. While the user is blocked the answer is 429 and `work` does not run."""
    app = request.app
    ip = _client_ip(request)
    with app.state.login_throttle.attempt(totp_keys(user.id), at) as attempt:
        if attempt.refused:
            if attempt.first_refusal:
                log.warning("code check blocked", extra={"user_id": str(user.id), "ip": ip})
            return error_response(
                429, "TOO_MANY_ATTEMPTS", "too many wrong codes", retry_after=attempt.retry_after
            )
        result = None
        try:
            with transaction(app, write=True) as db:
                result = work(db)
                if result is None:
                    raise _WrongCode  # leaves the transaction by an exception: rolled back
        except _WrongCode:
            pass
        except Exception:
            attempt.void()  # refused for another reason, or a failed commit: not a guess
            raise
        if result is not None:  # committed
            attempt.success()
            return result
    log.warning("code check: wrong code", extra={"user_id": str(user.id)})
    with transaction(app, write=True) as db:
        audit.record(
            db, "auth.two_factor_failed", actor_id=user.id, entity_type="user",
            entity_id=user.id, ip=ip,
        )  # fmt: skip
    return error_response(422, "TWO_FACTOR_CODE_WRONG", "wrong code")


def with_second_factor[T](
    request: Request,
    user: User,
    at: datetime,
    work: Callable[[Session], T],
    *,
    code: str | None,
    recovery_code: str | None,
) -> T | JSONResponse:
    """Run `work` in a write transaction, after the second factor of `user` when they have
    one: for an administrator's actions on another user (D-174). Call `wrong_password` first.

    With two-factor authentication on, a TOTP or recovery code is required (422
    `TWO_FACTOR_CODE_REQUIRED` without one) and checked as in `with_code`, in the same
    transaction as `work`. Without it (only possible with `PT_REQUIRE_2FA=off` for an
    administrator) the password alone is enough, and a code sent anyway is ignored."""
    app = request.app
    with transaction(app, write=False) as db:
        enabled = two_factor.is_enabled(db, user.id)
    if not enabled:
        with transaction(app, write=True) as db:
            if two_factor.is_enabled(db, user.id):  # enabled a moment ago: ask again
                return error_response(422, "TWO_FACTOR_CODE_REQUIRED", "a code is required")
            return work(db)
    if code is None and recovery_code is None:
        return error_response(422, "TWO_FACTOR_CODE_REQUIRED", "a code is required")

    def guarded(db: Session) -> T | None:
        if check(db, user.id, at, code=code, recovery_code=recovery_code) is None:
            return None
        return work(db)

    return with_code(request, user, at, guarded)
