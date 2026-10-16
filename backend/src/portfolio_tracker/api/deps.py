"""Request-scoped dependencies: database session, clock, authentication."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.auth.sessions import SessionPolicy, needs_touch, resolve, touch_best_effort
from portfolio_tracker.db import BEGIN_OPTION
from portfolio_tracker.db.models import User, UserSession

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SESSION_COOKIE = "pt_session"
# Cookie-authenticated writes must carry this header (ADR-0005). A cross-site form cannot
# set it, and a cross-site script cannot without a CORS preflight, which the app never allows.
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "portfolio-tracker"


@contextmanager
def transaction(app: FastAPI, *, write: bool) -> Iterator[Session]:
    """One database transaction, committed on normal exit, rolled back on an exception.

    A write transaction starts with BEGIN IMMEDIATE: it holds the write lock from the start,
    so a validate-then-write sequence cannot interleave with another writer. Keep it short:
    never hash a password inside one (D-82).
    """
    mode = "IMMEDIATE" if write else "DEFERRED"
    bind = app.state.engine.execution_options(**{BEGIN_OPTION: mode})
    with Session(bind, expire_on_commit=False) as session, session.begin():
        yield session


def get_session(request: Request) -> Iterator[Session]:
    """The request's transaction: a write transaction for every method but GET/HEAD/OPTIONS.

    Used with `scope="function"`: the commit runs when the handler returns and *before* the
    response is sent, so a failed commit is a 500, never a success that was not saved.
    """
    with transaction(request.app, write=request.method not in _READ_METHODS) as session:
        yield session


def get_read_session(request: Request) -> Iterator[Session]:
    """A read-only transaction whatever the method: for handlers that do slow work (password
    hashing) and open their own short write transaction afterwards."""
    with transaction(request.app, write=False) as session:
        yield session


DbSession = Annotated[Session, Depends(get_session, scope="function")]
ReadDbSession = Annotated[Session, Depends(get_read_session, scope="function")]


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


def authenticate(request: Request, db: Session, at: datetime, *, write_txn: bool) -> Auth:
    """Resolve the session of the request (CSRF-checked for cookie writes).

    `write_txn` says whether `db` holds the write lock: then last_used_at is refreshed in it;
    otherwise in a separate best-effort transaction, so a read never upgrades its own.
    """
    token, via_cookie = request_token(request)
    policy: SessionPolicy = request.app.state.session_policy
    found = resolve(db, token, at, policy) if token else None
    if found is None:
        raise ApiError(401, "NOT_AUTHENTICATED", "a valid session is required")
    writing = request.method not in _READ_METHODS
    if via_cookie and writing and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "CSRF_CHECK_FAILED", f"missing {CSRF_HEADER} header")
    session_row, user = found
    if needs_touch(session_row, at):
        if write_txn:
            session_row.last_used_at = at
            db.flush()
        else:
            touch_best_effort(request.app.state.engine, session_row.id, at)
    return Auth(user=user, session=session_row, via_cookie=via_cookie)


def authenticated_allow_password_change(request: Request, db: DbSession, at: Now) -> Auth:
    """A logged-in user, also one who still has to replace a temporary password."""
    return authenticate(request, db, at, write_txn=request.method not in _READ_METHODS)


AuthAllowPasswordChange = Annotated[Auth, Depends(authenticated_allow_password_change)]


def read_authenticated_allow_password_change(request: Request, db: ReadDbSession, at: Now) -> Auth:
    """Like `AuthAllowPasswordChange`, resolved in a read-only transaction (see
    `get_read_session`)."""
    return authenticate(request, db, at, write_txn=False)


ReadAuthAllowPasswordChange = Annotated[Auth, Depends(read_authenticated_allow_password_change)]


def _permanent_password(auth: Auth) -> Auth:
    """A user with a permanent password (temporary ones must be changed first, D-78)."""
    if auth.user.must_change_password:
        raise ApiError(403, "PASSWORD_CHANGE_REQUIRED", "the temporary password must be changed")
    return auth


def _administrator(auth: Auth) -> Auth:
    """Administrators only; anyone else gets 404, as if the endpoint did not exist."""
    if auth.user.role != "ADMINISTRATOR":
        raise not_found()
    return auth


def authenticated(auth: AuthAllowPasswordChange) -> Auth:
    return _permanent_password(auth)


CurrentAuth = Annotated[Auth, Depends(authenticated)]


def short_authenticated(request: Request, at: Now) -> Auth:
    """Like `CurrentAuth`, resolved in its own short read transaction that is closed before
    the handler runs: for handlers that do slow work (parsing an upload) and must not hold a
    pooled connection meanwhile. The handler opens its own transactions afterwards."""
    with transaction(request.app, write=False) as db:
        auth = authenticate(request, db, at, write_txn=False)
    return _permanent_password(auth)


ShortAuth = Annotated[Auth, Depends(short_authenticated)]


def administrator(auth: CurrentAuth) -> Auth:
    return _administrator(auth)


AdminAuth = Annotated[Auth, Depends(administrator)]


def read_administrator(auth: ReadAuthAllowPasswordChange) -> Auth:
    """Like `AdminAuth`, resolved in a read-only transaction: for admin actions that hash a
    password and then open their own short write transaction."""
    return _administrator(_permanent_password(auth))


ReadAdminAuth = Annotated[Auth, Depends(read_administrator)]
