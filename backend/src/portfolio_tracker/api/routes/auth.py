"""Setup wizard, login and logout (D-21, D-34, ADR-0005)."""

import logging
from datetime import datetime

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from portfolio_tracker.api.deps import (
    SESSION_COOKIE,
    AuthAllowPasswordChange,
    CurrentAuth,
    DbSession,
    Now,
)
from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.api.schemas import DisplayName, Language, MeOut, Strict, Username
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import Passwords, policy_error
from portfolio_tracker.auth.throttle import LoginThrottle
from portfolio_tracker.db.models import User
from portfolio_tracker.repositories import audit, users

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class SetupStatusOut(BaseModel):
    setup_required: bool  # true only while no user exists (D-34)


class CreateAdminIn(Strict):
    username: Username
    password: str = Field(max_length=4096)
    display_name: DisplayName | None = None
    language: Language


class LoginIn(Strict):
    username: str = Field(max_length=64)
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
    """Direct peer address. Forwarded headers are trusted only from configured proxies (M6)."""
    return request.client.host if request.client else None


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
    request: Request, response: Response, db: DbSession, user: User, at: datetime, token: bool
) -> LoginOut:
    policy = request.app.state.session_policy
    value, row = sessions.create_session(db, user, at, policy, request.headers.get("User-Agent"))
    user.last_login_at = at
    if not token:
        _warn_if_cookie_will_be_dropped(request)
        response.set_cookie(
            SESSION_COOKIE,
            value,
            max_age=int(policy.absolute.total_seconds()),
            httponly=True,
            secure=request.app.state.settings.cookie_secure,
            samesite="lax",
            path="/",
        )
    return LoginOut(user=MeOut.of(user), token=value if token else None, expires_at=row.expires_at)


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


@router.post("/setup/admin", status_code=status.HTTP_201_CREATED)
def create_first_administrator(
    body: CreateAdminIn, request: Request, response: Response, db: DbSession, at: Now
) -> LoginOut:
    """First run only: create the Administrator and log them in. Runs under the write lock,
    so two concurrent calls cannot both create one."""
    if users.any_user_exists(db):
        raise ApiError(409, "SETUP_ALREADY_DONE", "the instance already has users")
    if error := policy_error(body.password):
        raise ApiError(422, error, "password does not meet the policy")
    user = users.add_user(
        db,
        username=body.username,
        display_name=body.display_name,
        role="ADMINISTRATOR",
        password_hash=_passwords(request).hash(body.password),
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


@router.post("/setup/restore", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def restore_from_backup(db: DbSession) -> None:
    """Restore from a backup archive (D-34): available in M7."""
    if users.any_user_exists(db):
        raise ApiError(409, "SETUP_ALREADY_DONE", "the instance already has users")
    raise ApiError(501, "NOT_IMPLEMENTED", "restore from backup arrives in a later release")


@router.post(
    "/auth/login",
    response_model=LoginOut,
    responses={
        401: {"description": "INVALID_CREDENTIALS"},
        429: {"description": "TOO_MANY_ATTEMPTS"},
    },
)
def login(body: LoginIn, request: Request, response: Response, db: DbSession, at: Now):
    """Same answer and similar time for an unknown user, a disabled user and a wrong
    password; repeated failures are throttled per username and per IP."""
    throttle = _throttle(request)
    ip = client_ip(request)
    keys = throttle.keys(body.username, ip)
    if (wait := throttle.retry_after(keys, at)) is not None:
        log.warning("login blocked", extra={"username": body.username, "ip": ip})
        return _error(429, "TOO_MANY_ATTEMPTS", "too many failed logins", retry_after=wait)

    passwords = _passwords(request)
    user = users.get_by_username(db, body.username) if body.username else None
    if user is None or user.disabled_at is not None:
        passwords.verify_dummy(body.password)
        ok = False
    else:
        ok = passwords.verify(user.password_hash, body.password)

    if not ok:
        throttle.failure(keys, at)
        log.warning("login failed", extra={"username": body.username, "ip": ip})
        audit.record(
            db,
            "auth.login_failed",
            actor_id=None,
            after={"username": body.username},
            ip=ip,
        )
        # Returned, not raised: the audit entry must be committed with this request.
        return _error(401, "INVALID_CREDENTIALS", "wrong username or password")

    throttle.success(body.username)
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash(body.password)
    audit.record(db, "auth.login", actor_id=user.id, entity_type="user", entity_id=user.id, ip=ip)
    return _start_session(request, response, db, user, at, token=body.issue_token)


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


def _error(status_code: int, code: str, detail: str, **extra) -> JSONResponse:
    headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None
    return JSONResponse(
        status_code=status_code, content={"detail": detail, "code": code, **extra}, headers=headers
    )
