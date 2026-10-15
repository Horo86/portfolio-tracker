"""The logged-in user: profile, preferences, password, sessions (mockup 14)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from portfolio_tracker.api.deps import AuthAllowPasswordChange, CurrentAuth, DbSession, Now
from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import Currency, DisplayName, Language, MeOut, Strict, Theme
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import policy_error
from portfolio_tracker.db.models import UserSession
from portfolio_tracker.repositories import audit

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
def get_me(auth: AuthAllowPasswordChange) -> MeOut:
    return MeOut.of(auth.user)


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
    return MeOut.of(user)


@router.post("/password")
def change_password(
    body: PasswordChangeIn, auth: AuthAllowPasswordChange, request: Request, db: DbSession, at: Now
) -> MeOut:
    """Also replaces a temporary password (D-78). The other sessions are ended (mockup 15)."""
    user = auth.user
    passwords = request.app.state.passwords
    if not passwords.verify(user.password_hash, body.current_password):
        raise ApiError(422, "CURRENT_PASSWORD_WRONG", "the current password is wrong")
    if error := policy_error(body.new_password):
        raise ApiError(422, error, "password does not meet the policy")
    if body.new_password == body.current_password:
        raise ApiError(422, "PASSWORD_UNCHANGED", "the new password equals the current one")
    user.password_hash = passwords.hash(body.new_password)
    user.must_change_password = False
    user.password_changed_at = at
    revoked = sessions.revoke_all(db, user.id, except_id=auth.session.id)
    audit.record(
        db, "user.password_changed", actor_id=user.id, entity_type="user", entity_id=user.id,
        after={"other_sessions_revoked": revoked}, ip=client_ip(request),
    )  # fmt: skip
    return MeOut.of(user)


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
