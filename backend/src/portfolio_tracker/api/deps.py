"""Request-scoped dependencies: database session, clock, authentication."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy.orm import Session

from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.schemas import MeOut
from portfolio_tracker.auth.sessions import SessionPolicy, needs_touch, resolve, touch_best_effort
from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import User, UserSession
from portfolio_tracker.repositories import two_factor

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

SESSION_COOKIE = "pt_session"
# Cookie-authenticated writes must carry this header (ADR-0005). A cross-site form cannot
# set it, and a cross-site script cannot without a CORS preflight, which the app never allows.
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "portfolio-tracker"


@contextmanager
def transaction(app: FastAPI, *, write: bool) -> Iterator[Session]:
    """One database transaction of the app's engine (see `db.db_transaction`)."""
    with db_transaction(app.state.engine, write=write) as session:
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
    # An administrator without confirmed two-factor authentication while it is required
    # (D-174): only the enrolment, `GET /me`, the password change and logout are open.
    two_factor_setup_required: bool = False


def request_token(request: Request) -> tuple[str | None, bool]:
    """The session token and whether it came from the cookie. A bearer token wins."""
    scheme, _, credentials = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip(), False
    cookie = request.cookies.get(SESSION_COOKIE)
    return (cookie, True) if cookie else (None, False)


def require_csrf_header(request: Request) -> None:
    """R-15 (D-163): setup and login need the header too, from every client (cookie or
    bearer), so another site cannot post a login form or create the administrator."""
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise ApiError(403, "CSRF_CHECK_FAILED", f"missing {CSRF_HEADER} header")


CsrfChecked = [Depends(require_csrf_header)]


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
    return Auth(
        user=user,
        session=session_row,
        via_cookie=via_cookie,
        two_factor_setup_required=must_enrol(request, db, user),
    )


def must_enrol(request: Request, db: Session, user: User, enabled: bool | None = None) -> bool:
    """Whether `user` must enrol in two-factor authentication before anything else (D-174):
    an administrator without it, unless `PT_REQUIRE_2FA=off`. Read from the user's row and
    the database on every request, never from the session, so it holds for a session opened
    before an upgrade or before the role changed. Others cost no query."""
    if request.app.state.settings.require_2fa != "admins" or user.role != "ADMINISTRATOR":
        return False
    return not (two_factor.is_enabled(db, user.id) if enabled is None else enabled)


def me_out(request: Request, db: Session, user: User) -> MeOut:
    """The logged-in user as `/me` shows it, with where two-factor authentication stands."""
    enabled = two_factor.is_enabled(db, user.id)
    return MeOut.of(
        user,
        request.app.state.market_data.on,
        two_factor_enabled=enabled,
        two_factor_setup_required=must_enrol(request, db, user, enabled),
        recovery_codes_left=two_factor.recovery_codes_left(db, user.id) if enabled else None,
    )


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


def _enrolled(auth: Auth) -> Auth:
    """After the role and the temporary password (D-92, D-93): an administrator who must
    enrol in two-factor authentication does that first (D-174)."""
    if auth.two_factor_setup_required:
        raise ApiError(
            403, "TWO_FACTOR_SETUP_REQUIRED", "two-factor authentication must be set up first"
        )
    return auth


def _administrator(auth: Auth) -> Auth:
    """Administrators only; anyone else gets 404, as if the endpoint did not exist."""
    if auth.user.role != "ADMINISTRATOR":
        raise not_found()
    return auth


def authenticated(auth: AuthAllowPasswordChange) -> Auth:
    return _enrolled(_permanent_password(auth))


def read_authenticated(auth: ReadAuthAllowPasswordChange) -> Auth:
    """Like `CurrentAuth`, resolved in a read-only transaction: for handlers that check a
    password and then open their own short write transaction."""
    return _enrolled(_permanent_password(auth))


ReadCurrentAuth = Annotated[Auth, Depends(read_authenticated)]


def read_authenticated_allow_enrolment(auth: ReadAuthAllowPasswordChange) -> Auth:
    """A user with a permanent password, also an administrator who still has to enrol in
    two-factor authentication: for the enrolment itself (D-174). Read-only transaction."""
    return _permanent_password(auth)


ReadAuthAllowEnrolment = Annotated[Auth, Depends(read_authenticated_allow_enrolment)]


CurrentAuth = Annotated[Auth, Depends(authenticated)]


def short_authenticated(request: Request, at: Now) -> Auth:
    """Like `CurrentAuth`, resolved in its own short read transaction that is closed before
    the handler runs: for handlers that do slow work (parsing an upload) and must not hold a
    pooled connection meanwhile. The handler opens its own transactions afterwards."""
    with transaction(request.app, write=False) as db:
        auth = authenticate(request, db, at, write_txn=False)
    return _enrolled(_permanent_password(auth))


ShortAuth = Annotated[Auth, Depends(short_authenticated)]


def administrator(auth: AuthAllowPasswordChange) -> Auth:
    """The role first (D-92, D-93): a non-administrator gets 404 even while a password change
    is pending; only an administrator is then asked to change a temporary password, and
    after that to enrol in two-factor authentication (D-174)."""
    return _enrolled(_permanent_password(_administrator(auth)))


AdminAuth = Annotated[Auth, Depends(administrator)]


def read_administrator(auth: ReadAuthAllowPasswordChange) -> Auth:
    """Like `AdminAuth`, resolved in a read-only transaction: for admin actions that hash a
    password and then open their own short write transaction."""
    return _enrolled(_permanent_password(_administrator(auth)))


ReadAdminAuth = Annotated[Auth, Depends(read_administrator)]
