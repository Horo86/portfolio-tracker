"""Setup wizard, login and logout (D-21, D-34, ADR-0005)."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import (
    SESSION_COOKIE,
    AuthAllowPasswordChange,
    CsrfChecked,
    CurrentAuth,
    DbSession,
    Now,
    transaction,
)
from portfolio_tracker.api.errors import ApiError, error_response
from portfolio_tracker.api.schemas import (
    Email,
    Language,
    MeOut,
    PersonName,
    Strict,
    Username,
)
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import PasswordChecksBusy, Passwords, policy_error
from portfolio_tracker.auth.throttle import LoginThrottle, login_keys
from portfolio_tracker.db.models import User
from portfolio_tracker.identity import is_valid_username, normalize_username
from portfolio_tracker.repositories import audit, users

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class SetupStatusOut(BaseModel):
    setup_required: bool  # true only while no user exists (D-34)


class CreateAdminIn(Strict):
    username: Username
    password: str = Field(max_length=4096)
    first_name: PersonName
    last_name: PersonName
    email: Email
    language: Language


class LoginIn(Strict):
    username: str = Field(max_length=256)  # found by its normalised key (D-173)
    password: str = Field(max_length=4096)
    # Future native clients ask for the token in the body (bearer); the web app gets only
    # the HttpOnly cookie, so page scripts never see the token.
    issue_token: bool = False


class LoginOut(BaseModel):
    user: MeOut
    token: str | None = None
    expires_at: datetime


class RevokedOut(BaseModel):
    revoked: int


def client_ip(request: Request) -> str | None:
    """The client's address, as `ForwardedHeaders` resolved it (`api/forwarded.py`, D-164):
    the direct peer unless that peer is a trusted proxy."""
    return request.client.host if request.client else None


def loggable_username(user: User | None, typed: str) -> str:
    """What a failed or blocked login records of the typed username: the user's own username
    when it names one, else `(unknown)`, or `(invalid)` when it has no username's shape. People
    sometimes type their password in the username field, and since usernames may carry
    `@ + . _ -` (D-173) many passwords have a username's shape: only an existing username is
    ever written, never the text typed for an unknown one."""
    if user is not None:
        return user.username
    return "(unknown)" if is_valid_username(normalize_username(typed)) else "(invalid)"


def _user_named(request: Request, typed: str) -> User | None:
    with transaction(request.app, write=False) as db:
        return users.get_by_username(db, typed) if typed else None


def _passwords(request: Request) -> Passwords:
    return request.app.state.passwords


def _throttle(request: Request) -> LoginThrottle:
    return request.app.state.login_throttle


def _warn_if_cookie_will_be_dropped(request: Request) -> None:
    """Browsers drop Secure cookies over plain http except on localhost: say why login
    'does nothing' instead of leaving the administrator guessing (M2 plan, step 5)."""
    state = request.app.state
    if not state.settings.cookie_secure or state.cookie_warning_logged:
        return
    if request.url.scheme == "http" and (request.url.hostname or "") not in _LOCAL_HOSTS:
        state.cookie_warning_logged = True
        log.warning(
            "login over plain http with PT_COOKIE_SECURE=true: browsers will not keep the "
            "session cookie. Serve the app over https (reverse proxy) or, on a trusted LAN "
            "only, set PT_COOKIE_SECURE=false",
            extra={"host": request.url.hostname},
        )


def _start_session(
    request: Request, response: Response, db: Session, user: User, at: datetime, token: bool
) -> LoginOut:
    """A new session. The one carried by the incoming cookie, if any, ends first, whoever it
    belongs to (D-164): no fixed session value, no orphan when another user logs in."""
    policy = request.app.state.session_policy
    incoming = request.cookies.get(SESSION_COOKIE)
    if incoming:
        sessions.revoke_token(db, incoming)
    value, row = sessions.create_session(db, user, at, policy, request.headers.get("User-Agent"))
    user.last_login_at = at
    if not token:
        _warn_if_cookie_will_be_dropped(request)
        set_session_cookie(request, response, value, policy.absolute.total_seconds())
    elif incoming:
        _clear_cookie(request, response)  # it pointed at the session just ended
    user_out = MeOut.of(user, request.app.state.market_data.on)
    return LoginOut(user=user_out, token=value if token else None, expires_at=row.expires_at)


def set_session_cookie(request: Request, response: Response, value: str, max_age: float) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=int(max_age),
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
    )


@router.get("/setup/status")
def setup_status(db: DbSession) -> SetupStatusOut:
    return SetupStatusOut(setup_required=not users.any_user_exists(db))


@router.post("/setup/admin", status_code=status.HTTP_201_CREATED, dependencies=CsrfChecked)
def create_first_administrator(
    body: CreateAdminIn, request: Request, response: Response, at: Now
) -> LoginOut:
    """First run only: create the Administrator and log them in. The password is hashed
    outside any transaction; the write transaction checks again that no user exists, so two
    concurrent calls cannot both create one."""
    app = request.app
    with transaction(app, write=False) as db:
        if users.any_user_exists(db):
            raise _setup_done()
    if error := policy_error(body.password):
        raise ApiError(422, error, "password does not meet the policy")
    password_hash = _passwords(request).hash(body.password)
    with transaction(app, write=True) as db:
        if users.any_user_exists(db):
            raise _setup_done()
        user = users.add_user(
            db,
            username=body.username,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            role="ADMINISTRATOR",
            password_hash=password_hash,
            language=body.language,
            password_changed_at=at,
            created_at=at,
        )
        audit.record(
            db,
            "setup.administrator_created",
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            after={"username": user.username},
            ip=client_ip(request),
        )
        return _start_session(request, response, db, user, at, token=False)


def _setup_done() -> ApiError:
    return ApiError(409, "SETUP_ALREADY_DONE", "the instance already has users")


@router.post(
    "/setup/restore", status_code=status.HTTP_501_NOT_IMPLEMENTED, dependencies=CsrfChecked
)
def restore_from_backup(db: DbSession) -> None:
    """Restore from the wizard is a later version's; in 1.0.0 it is the `restore` command
    (D-169). The CSRF header is already required, as on `/setup/admin` (D-163)."""
    if users.any_user_exists(db):
        raise _setup_done()
    raise ApiError(501, "NOT_IMPLEMENTED", "restore from backup arrives in a later release")


@router.post(
    "/auth/login",
    dependencies=CsrfChecked,
    response_model=LoginOut,
    responses={
        401: {"description": "INVALID_CREDENTIALS"},
        429: {"description": "TOO_MANY_ATTEMPTS"},
    },
)
def login(body: LoginIn, request: Request, response: Response, at: Now):
    """Same answer and similar time for an unknown user, a disabled user and a wrong
    password; failures are throttled per username and per IP (D-82).

    The password is verified outside any transaction: a slow Argon2 check never holds the
    SQLite write lock. The attempt is reserved before the check, so parallel requests
    cannot exceed the free attempts.
    """
    app = request.app
    ip = client_ip(request)
    with _throttle(request).attempt(login_keys(body.username, ip), at) as attempt:
        if attempt.refused:
            if attempt.first_refusal:  # once per block, so a flood cannot fill the logs
                name = loggable_username(_user_named(request, body.username), body.username)
                log.warning("login blocked", extra={"username": name, "ip": ip})
            return error_response(
                429, "TOO_MANY_ATTEMPTS", "too many failed logins", retry_after=attempt.retry_after
            )

        passwords = _passwords(request)
        user = _user_named(request, body.username)
        name = loggable_username(user, body.username)
        found = (user.id, user.password_hash) if user and not user.disabled_at else None
        try:
            if found is None:
                passwords.verify_dummy(body.password)
                verified = False
            else:
                verified = passwords.verify(found[1], body.password)
            rehash = verified and passwords.needs_rehash(found[1])
            new_hash = passwords.hash(body.password) if rehash else None
        except PasswordChecksBusy:
            attempt.void()  # no slot: the password was never checked (D-162)
            raise
        if verified:
            result = None
            with transaction(app, write=True) as db:
                user = users.get_user(db, found[0])
                # Disabled or re-hashed by someone else meanwhile: treat as a failure.
                if user and not user.disabled_at and user.password_hash == found[1]:
                    if new_hash:
                        user.password_hash = new_hash
                    audit.record(
                        db, "auth.login", actor_id=user.id, entity_type="user",
                        entity_id=user.id, ip=ip,
                    )  # fmt: skip
                    result = _start_session(request, response, db, user, at, body.issue_token)
            if result is not None:  # committed
                attempt.success()
                return result

    log.warning("login failed", extra={"username": name, "ip": ip})
    with transaction(app, write=True) as db:
        audit.record(db, "auth.login_failed", actor_id=None, after={"username": name}, ip=ip)
    return error_response(401, "INVALID_CREDENTIALS", "wrong username or password")


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: AuthAllowPasswordChange, request: Request, response: Response, db: DbSession):
    sessions.revoke(db, auth.session)
    audit.record(db, "auth.logout", actor_id=auth.user.id, ip=client_ip(request))
    _clear_cookie(request, response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/auth/logout-others")
def logout_other_sessions(auth: CurrentAuth, request: Request, db: DbSession) -> RevokedOut:
    """ "Log out from the other devices" (mockup 14): keeps the current session."""
    revoked = sessions.revoke_all(db, auth.user.id, except_id=auth.session.id)
    audit.record(
        db, "auth.logout_others", actor_id=auth.user.id, after={"revoked": revoked},
        ip=client_ip(request),
    )  # fmt: skip
    return RevokedOut(revoked=revoked)
