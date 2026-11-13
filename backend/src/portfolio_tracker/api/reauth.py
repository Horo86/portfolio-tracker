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
from portfolio_tracker.repositories import audit

log = logging.getLogger(__name__)


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
    returns its result, or None when the code was wrong: then nothing it did is kept
    (it must not have changed anything), the guess is counted under the user's `totp` key and
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
        try:
            with transaction(app, write=True) as db:
                result = work(db)
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
