"""Request-scoped dependencies: database session, clock, authentication."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.auth.sessions import SessionPolicy, resolve
from portfolio_tracker.db import BEGIN_OPTION
from portfolio_tracker.db.models import User, UserSession

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SESSION_COOKIE = "pt_session"
# Cookie-authenticated writes must carry this header (ADR-0005). A cross-site form cannot
# set it, and a cross-site script cannot without a CORS preflight, which the app never allows.
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "portfolio-tracker"


def get_session(request: Request) -> Iterator[Session]:
    """One database transaction per request, committed when the handler returns.

    Write requests start with BEGIN IMMEDIATE: validation and persistence run under the
    write lock, so two concurrent changes to a ledger cannot both pass validation.
    """
    mode = "DEFERRED" if request.method in _READ_METHODS else "IMMEDIATE"
    bind = request.app.state.engine.execution_options(**{BEGIN_OPTION: mode})
    with Session(bind, expire_on_commit=False) as session, session.begin():
        yield session


DbSession = Annotated[Session, Depends(get_session)]


def now(request: Request) -> datetime:
    return request.app.state.clock()


Now = Annotated[datetime, Depends(now)]


@dataclass(frozen=True, slots=True)
class Auth:
    user: User
    session: UserSession
    via_cookie: bool


def request_token(request: Request) -> tuple[str | None, bool]:
    """The session token and whether it came from the cookie. A bearer token wins."""
    scheme, _, credentials = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip(), False
    cookie = request.cookies.get(SESSION_COOKIE)
    return (cookie, True) if cookie else (None, False)


def authenticated_allow_password_change(request: Request, db: DbSession, at: Now) -> Auth:
    """A logged-in user, also one who still has to replace a temporary password."""
    token, via_cookie = request_token(request)
    policy: SessionPolicy = request.app.state.session_policy
    found = resolve(db, token, at, policy) if token else None
    if found is None:
        raise ApiError(401, "NOT_AUTHENTICATED", "a valid session is required")
    writing = request.method not in _READ_METHODS
    if via_cookie and writing and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "CSRF_CHECK_FAILED", f"missing {CSRF_HEADER} header")
    session_row, user = found
    return Auth(user=user, session=session_row, via_cookie=via_cookie)


AuthAllowPasswordChange = Annotated[Auth, Depends(authenticated_allow_password_change)]


def authenticated(auth: AuthAllowPasswordChange) -> Auth:
    """A logged-in user with a permanent password (temporary ones must be changed, D-78)."""
    if auth.user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "the temporary password must be changed")
    return auth


CurrentAuth = Annotated[Auth, Depends(authenticated)]


def administrator(auth: CurrentAuth) -> Auth:
    """Administrators only; anyone else gets 404, as if the endpoint did not exist."""
    if auth.user.role != "ADMINISTRATOR":
        raise not_found()
    return auth


AdminAuth = Annotated[Auth, Depends(administrator)]
