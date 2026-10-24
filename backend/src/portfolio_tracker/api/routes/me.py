"""The logged-in user: profile, preferences, password, sessions (mockup 14)."""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from portfolio_tracker.api.deps import (
    AuthAllowPasswordChange,
    CurrentAuth,
    DbSession,
    Now,
    ReadAuthAllowPasswordChange,
    transaction,
)
from portfolio_tracker.api.errors import ApiError, error_response
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import Currency, DisplayName, Language, MeOut, Strict, Theme
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import policy_error
from portfolio_tracker.auth.throttle import password_change_keys
from portfolio_tracker.db.models import UserSession
from portfolio_tracker.repositories import audit, users

log = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["me"])


class MePatch(Strict):
    display_name: DisplayName | None = None
    language: Language | None = None
    theme: Theme | None = None
    reporting_currency: Currency | None = None


class PasswordChangeIn(Strict):
    current_password: str = Field(max_length=4096)
    new_password: str = Field(max_length=4096)


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    user_agent: str | None
    current: bool


@router.get("")
def get_me(auth: AuthAllowPasswordChange, request: Request) -> MeOut:
    return MeOut.of(auth.user, request.app.state.market_data.on)


@router.patch("")
def update_me(body: MePatch, auth: CurrentAuth, request: Request, db: DbSession) -> MeOut:
    """Only the fields sent are changed; `display_name: null` clears it."""
    user = auth.user
    changes = body.model_dump(exclude_unset=True)
    for name in ("language", "theme", "reporting_currency"):
        if name in changes and changes[name] is None:
            raise ApiError(422, "REQUEST_INVALID", f"{name} cannot be null")
    before = {name: getattr(user, name) for name in changes}
    for name, value in changes.items():
        setattr(user, name, value)
    db.flush()
    audit.record(
        db, "user.preferences_updated", actor_id=user.id, entity_type="user", entity_id=user.id,
        before=before, after=changes, ip=client_ip(request),
    )  # fmt: skip
    return MeOut.of(user, request.app.state.market_data.on)


@router.post(
    "/password",
    responses={422: {"description": "CURRENT_PASSWORD_WRONG, PASSWORD_TOO_SHORT, ..."},
               429: {"description": "TOO_MANY_ATTEMPTS"}},
)  # fmt: skip
def change_password(
    body: PasswordChangeIn, auth: ReadAuthAllowPasswordChange, request: Request, at: Now
):
    """Also replaces a temporary password (D-78). The other sessions are ended (mockup 15).

    Guesses of the current password are throttled per user and audited (D-82), so a stolen
    session cannot be used to find the password. Hashing runs outside any write transaction.
    """
    app = request.app
    user = auth.user
    passwords = app.state.passwords
    ip = client_ip(request)
    with app.state.login_throttle.attempt(password_change_keys(user.id), at) as attempt:
        if attempt.refused:
            if attempt.first_refusal:
                log.warning("password change blocked", extra={"user_id": str(user.id), "ip": ip})
            return error_response(
                429,
                "TOO_MANY_ATTEMPTS",
                "too many wrong passwords",
                retry_after=attempt.retry_after,
            )
        if not passwords.verify(user.password_hash, body.current_password):
            log.warning("password change: wrong current password", extra={"user_id": str(user.id)})
            with transaction(app, write=True) as db:
                audit.record(
                    db, "user.password_change_failed", actor_id=user.id, entity_type="user",
                    entity_id=user.id, ip=ip,
                )  # fmt: skip
            return error_response(422, "CURRENT_PASSWORD_WRONG", "the current password is wrong")
        attempt.success()  # the current password is proven; what follows is not a guess

    if error := policy_error(body.new_password):
        raise ApiError(422, error, "password does not meet the policy")
    if body.new_password == body.current_password:
        raise ApiError(422, "PASSWORD_UNCHANGED", "the new password equals the current one")
    new_hash = passwords.hash(body.new_password)

    with transaction(app, write=True) as db:
        fresh = users.get_user(db, user.id)
        if fresh is None or fresh.disabled_at or db.get(UserSession, auth.session.id) is None:
            raise ApiError(401, "NOT_AUTHENTICATED", "a valid session is required")
        if fresh.password_hash != user.password_hash:
            raise ApiError(409, "PASSWORD_CHANGED_MEANWHILE", "the password was changed meanwhile")
        fresh.password_hash = new_hash
        fresh.must_change_password = False
        fresh.password_changed_at = at
        revoked = sessions.revoke_all(db, fresh.id, except_id=auth.session.id)
        audit.record(
            db, "user.password_changed", actor_id=fresh.id, entity_type="user", entity_id=fresh.id,
            after={"other_sessions_revoked": revoked}, ip=ip,
        )  # fmt: skip
        return MeOut.of(fresh, app.state.market_data.on)


@router.get("/sessions")
def list_sessions(auth: CurrentAuth, db: DbSession) -> list[SessionOut]:
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == auth.user.id)
        .order_by(UserSession.last_used_at.desc())
    )
    return [
        SessionOut(
            id=row.id,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            user_agent=row.user_agent,
            current=row.id == auth.session.id,
        )
        for row in rows
    ]
