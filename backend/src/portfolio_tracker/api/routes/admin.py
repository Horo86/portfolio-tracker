"""User management for the Administrator (D-21, D-51, D-78; mockups 15 and 17).

Only account data of users is exposed here: never accounts, assets, transactions or figures
of other users (D-33). Non-administrators get 404 on every endpoint.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from portfolio_tracker.api.deps import AdminAuth, Auth, DbSession, Now, ReadAdminAuth, transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import DisplayName, Language, Role, Strict, Username
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import temporary_password
from portfolio_tracker.db.models import User
from portfolio_tracker.repositories import audit, users

router = APIRouter(prefix="/admin", tags=["administration"])


class AdminUserOut(BaseModel):
    id: UUID
    username: str
    display_name: str | None
    role: Role
    two_factor_enabled: bool  # always false until M6
    must_change_password: bool
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User) -> "AdminUserOut":
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            two_factor_enabled=False,
            must_change_password=user.must_change_password,
            disabled=user.disabled_at is not None,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class CreateUserIn(Strict):
    username: Username
    role: Role = "INVESTOR"
    display_name: DisplayName | None = None
    language: Language | None = None  # default: the administrator's language


class TemporaryPasswordOut(BaseModel):
    """The temporary password is returned only in this response (D-78)."""

    user: AdminUserOut
    temporary_password: str


def _audit(db, action: str, auth: Auth, user: User, request: Request, **after) -> None:
    audit.record(
        db,
        action,
        actor_id=auth.user.id,
        entity_type="user",
        entity_id=user.id,
        after={"username": user.username, **after},
        ip=client_ip(request),
    )


@router.get("/users")
def list_users(auth: AdminAuth, db: DbSession) -> list[AdminUserOut]:
    return [AdminUserOut.of(user) for user in users.list_users(db)]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserIn, auth: ReadAdminAuth, request: Request, at: Now
) -> TemporaryPasswordOut:
    """The new user logs in with the temporary password and must change it (D-78)."""
    password = temporary_password()
    password_hash = request.app.state.passwords.hash(password)  # outside the write lock
    with transaction(request.app, write=True) as db:
        if users.get_by_username(db, body.username) is not None:
            raise ApiError(409, "USERNAME_TAKEN", "the username is already in use")
        user = users.add_user(
            db,
            username=body.username,
            display_name=body.display_name,
            role=body.role,
            password_hash=password_hash,
            must_change_password=True,
            language=body.language or auth.user.language,
            password_changed_at=at,
            created_at=at,
        )
        _audit(db, "user.created", auth, user, request, role=user.role)
        return TemporaryPasswordOut(user=AdminUserOut.of(user), temporary_password=password)


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: UUID, auth: ReadAdminAuth, request: Request, at: Now
) -> TemporaryPasswordOut:
    """A new temporary password (shown once); every session of the user is ended."""
    password = temporary_password()
    password_hash = request.app.state.passwords.hash(password)
    with transaction(request.app, write=True) as db:
        user = users.get_user(db, user_id) or _missing()
        user.password_hash = password_hash
        user.must_change_password = True
        user.password_changed_at = at
        revoked = sessions.revoke_all(db, user.id)
        _audit(db, "user.password_reset", auth, user, request, sessions_revoked=revoked)
        return TemporaryPasswordOut(user=AdminUserOut.of(user), temporary_password=password)


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: UUID, auth: AdminAuth, request: Request, db: DbSession, at: Now
) -> AdminUserOut:
    """A disabled user cannot log in and loses every session; their data is kept."""
    user = users.get_user(db, user_id) or _missing()
    if user.id == auth.user.id:
        raise ApiError(409, "CANNOT_DISABLE_SELF", "administrators cannot disable themselves")
    if user.disabled_at is None:
        if user.role == "ADMINISTRATOR" and users.count_active_administrators(db) <= 1:
            raise ApiError(409, "LAST_ADMINISTRATOR", "the last administrator cannot be disabled")
        user.disabled_at = at
        revoked = sessions.revoke_all(db, user.id)
        _audit(db, "user.disabled", auth, user, request, sessions_revoked=revoked)
    return AdminUserOut.of(user)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: UUID, auth: AdminAuth, request: Request, db: DbSession) -> AdminUserOut:
    user = users.get_user(db, user_id) or _missing()
    if user.disabled_at is not None:
        user.disabled_at = None
        _audit(db, "user.enabled", auth, user, request)
    return AdminUserOut.of(user)


def _missing() -> User:
    raise not_found("user")
