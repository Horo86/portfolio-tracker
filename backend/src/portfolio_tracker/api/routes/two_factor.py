"""The user's own two-factor authentication (D-174): enrol, disable, new recovery codes.

Enabling asks for the password before the secret is shown, and is confirmed with a code.
Disabling and new recovery codes ask for the password and a TOTP or recovery code. Each ends
the user's other sessions and gives the current one a new token. The secret and the recovery
codes are returned once, in the answer that creates them, and never again; nothing here logs
them or puts them in an error.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import (
    Auth,
    CurrentAuth,
    DbSession,
    Now,
    ReadAuthAllowEnrolment,
    ReadCurrentAuth,
    me_out,
    transaction,
)
from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.api.reauth import with_code, wrong_password
from portfolio_tracker.api.routes.auth import client_ip, set_session_cookie
from portfolio_tracker.api.schemas import MeOut, Strict
from portfolio_tracker.auth import recovery, sessions, totp
from portfolio_tracker.db.models import User, UserSession
from portfolio_tracker.repositories import audit, two_factor, users
from portfolio_tracker.services.two_factor import check

router = APIRouter(prefix="/me/two-factor", tags=["me"])

_ERRORS = {
    422: {"description": "CURRENT_PASSWORD_WRONG, TWO_FACTOR_CODE_WRONG"},
    429: {"description": "TOO_MANY_ATTEMPTS"},
}


class PasswordIn(Strict):
    password: str = Field(max_length=4096, repr=False)


class CodeIn(Strict):
    code: str = Field(max_length=32, repr=False)


class PasswordAndCodeIn(Strict):
    """The password and a TOTP code or a recovery code, never both codes."""

    password: str = Field(max_length=4096, repr=False)
    code: str | None = Field(default=None, max_length=32, repr=False)
    recovery_code: str | None = Field(default=None, max_length=64, repr=False)

    @model_validator(mode="after")
    def _one_factor(self) -> "PasswordAndCodeIn":
        if (self.code is None) == (self.recovery_code is None):
            raise ValueError("give either code or recovery_code")
        return self


class SetupOut(BaseModel):
    """Shown once: the secret as text, the address an authenticator app reads, and that
    address as a QR code (rows of `0` and `1`, 1 dark), which the page draws itself."""

    secret: str = Field(repr=False)
    otpauth_uri: str = Field(repr=False)
    qr: list[str] = Field(repr=False)
    digits: Literal[6] = 6
    period: Literal[30] = 30
    server_time: datetime  # codes depend on the server's clock: shown, to compare with a phone


class ChangedOut(BaseModel):
    user: MeOut
    # The current session's new token, for a bearer client only (as the password change,
    # D-164); the web app gets it in the HttpOnly cookie.
    token: str | None = Field(default=None, repr=False)


class RecoveryCodesOut(ChangedOut):
    recovery_codes: list[str] = Field(repr=False)  # shown once, never stored as text


def _current(db: Session, auth: Auth) -> tuple[User, UserSession]:
    """The user and the session of `auth` as they are now, inside the write transaction: the
    password may have been checked a moment ago, outside it."""
    user = users.get_user(db, auth.user.id)
    session = db.get(UserSession, auth.session.id)
    if user is None or user.disabled_at or session is None:
        raise ApiError(401, "NOT_AUTHENTICATED", "a valid session is required")
    if user.password_hash != auth.user.password_hash:
        raise ApiError(409, "PASSWORD_CHANGED_MEANWHILE", "the password was changed meanwhile")
    return user, session


def _other_sessions_end(
    db: Session,
    request: Request,
    response: Response,
    auth: Auth,
    session: UserSession,
    at: datetime,
) -> tuple[int, str | None]:
    """End the user's other sessions and give this one a new token. Returns how many ended,
    and the token when the client is a bearer one (the web app gets the cookie)."""
    revoked = sessions.revoke_all(db, session.user_id, except_id=session.id)
    token = sessions.rotate(db, session)
    if auth.via_cookie:
        set_session_cookie(request, response, token, (session.expires_at - at).total_seconds())
        return revoked, None
    return revoked, token


def _new_recovery_codes(db: Session, user: User, at: datetime) -> list[str]:
    codes = recovery.new_codes()
    two_factor.replace_recovery_codes(db, user.id, map(recovery.hash_code, codes), at)
    return codes


@router.post("/setup", responses={**_ERRORS, 409: {"description": "TWO_FACTOR_ALREADY_ENABLED"}})
def start_enrolment(
    body: PasswordIn, auth: ReadAuthAllowEnrolment, request: Request, at: Now
) -> SetupOut:
    """Make a new secret, after the password. Nothing is on until `confirm`; starting again
    replaces a secret that was never confirmed."""
    refused = wrong_password(
        request, auth.user, body.password, at,
        what="two-factor setup", action="user.two_factor_password_failed",
    )  # fmt: skip
    if refused is not None:
        return refused
    with transaction(request.app, write=True) as db:
        user, _ = _current(db, auth)
        if two_factor.is_enabled(db, user.id):
            raise ApiError(409, "TWO_FACTOR_ALREADY_ENABLED", "two-factor authentication is on")
        secret = totp.new_secret()
        two_factor.begin(db, user.id, secret, at)
        audit.record(
            db, "user.two_factor_setup_started", actor_id=user.id, entity_type="user",
            entity_id=user.id, ip=client_ip(request),
        )  # fmt: skip
        uri = totp.provisioning_uri(secret, user.username)
        return SetupOut(secret=secret, otpauth_uri=uri, qr=totp.qr_matrix(uri), server_time=at)


_CONFIRM_409 = {"description": "TWO_FACTOR_ALREADY_ENABLED, TWO_FACTOR_NOT_STARTED"}


@router.post("/confirm", responses={**_ERRORS, 409: _CONFIRM_409})
def confirm_enrolment(
    body: CodeIn, auth: ReadAuthAllowEnrolment, request: Request, response: Response, at: Now
) -> RecoveryCodesOut:
    """The first code from the authenticator app turns two-factor authentication on and
    gives the ten recovery codes, once."""

    def work(db: Session) -> RecoveryCodesOut | None:
        user, session = _current(db, auth)
        row = two_factor.get(db, user.id)
        # No pending secret, or one shown too long ago to be confirmed still (D-174).
        if row is None or (
            row.confirmed_at is None and at - row.created_at >= totp.PENDING_LIFETIME
        ):
            raise ApiError(409, "TWO_FACTOR_NOT_STARTED", "start the setup first")
        if row.confirmed_at is not None:
            raise ApiError(409, "TWO_FACTOR_ALREADY_ENABLED", "two-factor authentication is on")
        step = totp.matching_step(row.secret, body.code, at, row.last_step)
        if step is None or not two_factor.accept_step(db, user.id, step):
            return None
        two_factor.confirm(db, user.id, at)
        codes = _new_recovery_codes(db, user, at)
        user.two_factor_disabled_at = None  # the notice ends when it is on again
        revoked, token = _other_sessions_end(db, request, response, auth, session, at)
        audit.record(
            db, "user.two_factor_enabled", actor_id=user.id, entity_type="user",
            entity_id=user.id, after={"other_sessions_revoked": revoked}, ip=client_ip(request),
        )  # fmt: skip
        return RecoveryCodesOut(user=me_out(request, db, user), token=token, recovery_codes=codes)

    return with_code(request, auth.user, at, work)


def _second_factor(db: Session, user: User, body: PasswordAndCodeIn, at: datetime) -> bool:
    if not two_factor.is_enabled(db, user.id):
        raise ApiError(409, "TWO_FACTOR_NOT_ENABLED", "two-factor authentication is off")
    factor = check(db, user.id, at, code=body.code, recovery_code=body.recovery_code)
    return factor is not None


@router.post("/disable", responses={**_ERRORS, 409: {"description": "TWO_FACTOR_NOT_ENABLED"}})
def disable(
    body: PasswordAndCodeIn, auth: ReadCurrentAuth, request: Request, response: Response, at: Now
) -> ChangedOut:
    """Turn two-factor authentication off: the secret and the recovery codes are deleted.
    An administrator who must have it is then asked to enrol again."""
    refused = wrong_password(
        request, auth.user, body.password, at,
        what="two-factor disable", action="user.two_factor_password_failed",
    )  # fmt: skip
    if refused is not None:
        return refused

    def work(db: Session) -> ChangedOut | None:
        user, session = _current(db, auth)
        if not _second_factor(db, user, body, at):
            return None
        two_factor.remove(db, user.id)
        request.app.state.login_challenges.drop_user(user.id)  # a login waiting for its code
        revoked, token = _other_sessions_end(db, request, response, auth, session, at)
        audit.record(
            db, "user.two_factor_disabled", actor_id=user.id, entity_type="user",
            entity_id=user.id, after={"by": "user", "other_sessions_revoked": revoked},
            ip=client_ip(request),
        )  # fmt: skip
        return ChangedOut(user=me_out(request, db, user), token=token)

    return with_code(request, auth.user, at, work)


@router.post(
    "/recovery-codes", responses={**_ERRORS, 409: {"description": "TWO_FACTOR_NOT_ENABLED"}}
)
def new_recovery_codes(
    body: PasswordAndCodeIn, auth: ReadCurrentAuth, request: Request, response: Response, at: Now
) -> RecoveryCodesOut:
    """Ten new recovery codes, shown once; every old one stops working."""
    refused = wrong_password(
        request, auth.user, body.password, at,
        what="new recovery codes", action="user.two_factor_password_failed",
    )  # fmt: skip
    if refused is not None:
        return refused

    def work(db: Session) -> RecoveryCodesOut | None:
        user, session = _current(db, auth)
        if not _second_factor(db, user, body, at):
            return None
        codes = _new_recovery_codes(db, user, at)
        revoked, token = _other_sessions_end(db, request, response, auth, session, at)
        audit.record(
            db, "user.recovery_codes_replaced", actor_id=user.id, entity_type="user",
            entity_id=user.id, after={"other_sessions_revoked": revoked}, ip=client_ip(request),
        )  # fmt: skip
        return RecoveryCodesOut(user=me_out(request, db, user), token=token, recovery_codes=codes)

    return with_code(request, auth.user, at, work)


@router.post("/notice/dismiss")
def dismiss_notice(auth: CurrentAuth, request: Request, db: DbSession) -> MeOut:
    """ "Ho capito": the notice that two-factor authentication was removed by an
    administrator or the container command is not shown again."""
    user = auth.user
    if user.two_factor_disabled_at is not None:
        user.two_factor_disabled_at = None
        db.flush()
        audit.record(
            db, "user.two_factor_notice_dismissed", actor_id=user.id, entity_type="user",
            entity_id=user.id, ip=client_ip(request),
        )  # fmt: skip
    return me_out(request, db, user)
