"""User management for the Administrator (D-21, D-51, D-78; mockups 15 and 17).

Only account data of users is exposed here: never accounts, assets, transactions or figures
of other users (D-33). Non-administrators get 404 on every endpoint.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from portfolio_tracker import __version__
from portfolio_tracker.api.deps import AdminAuth, Auth, DbSession, Now, ReadAdminAuth, transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import DisplayName, Language, Role, Strict, Username
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import temporary_password
from portfolio_tracker.backup import latest_backup
from portfolio_tracker.db.models import User
from portfolio_tracker.migrations_runner import current_revision, head_revision, list_copies
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


class UpgradeOut(BaseModel):
    """The newest upgrade with a record (D-168 (6))."""

    from_revision: str | None
    to_revision: str | None
    version: str | None  # the version that upgraded
    at: str | None
    status: str | None  # done, failed, pending
    copy_path: str  # relative to the data folder


class CopyOut(BaseModel):
    path: str  # relative to the data folder
    size: int
    at: str
    status: str | None  # none for a copy made by 0.9.0 (no record file)


class BackupOut(BaseModel):
    path: str  # relative to the data folder
    size: int
    at: str


class SystemOut(BaseModel):
    """What an administrator needs to see after an upgrade: file names relative to the data
    folder and sizes only, never an absolute path, data or figures."""

    version: str
    schema_revision: str
    schema_head: str
    last_upgrade: UpgradeOut | None
    pre_migration_copies: list[CopyOut]
    latest_backup: BackupOut | None


@router.get("/system")
def system(auth: AdminAuth, request: Request) -> SystemOut:
    settings = request.app.state.settings
    data_dir = settings.data_dir

    def relative(path) -> str:
        return path.relative_to(data_dir).as_posix()

    copies = list_copies(settings.pre_migration_backup_dir)
    recorded = next((copy for copy in copies if copy.record), None)
    last = None
    if recorded is not None:
        record = recorded.record or {}
        last = UpgradeOut(
            from_revision=record.get("from"),
            to_revision=record.get("to"),
            version=record.get("version"),
            at=record.get("at"),
            status=record.get("status"),
            copy_path=relative(recorded.path),
        )
    backup = latest_backup(settings.manual_backup_dir)
    return SystemOut(
        version=__version__,
        schema_revision=current_revision(request.app.state.engine),
        schema_head=head_revision(),
        last_upgrade=last,
        pre_migration_copies=[
            CopyOut(
                path=relative(copy.path),
                size=copy.size,
                at=copy.at,
                status=(copy.record or {}).get("status"),
            )
            for copy in copies
        ],
        latest_backup=(
            BackupOut(path=relative(backup.path), size=backup.size, at=backup.at)
            if backup
            else None
        ),
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
        user.password_reset_at = at  # the notice at the next login (D-165)
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
