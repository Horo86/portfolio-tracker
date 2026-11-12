"""User management for the Administrator (D-21, D-51, D-78; mockups 15 and 17).

Only account data of users is exposed here: never accounts, assets, transactions or figures
of other users (D-33). Non-administrators get 404 on every endpoint.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from portfolio_tracker import __version__
from portfolio_tracker.api.deps import AdminAuth, Auth, DbSession, Now, ReadAdminAuth, transaction
from portfolio_tracker.api.errors import ApiError, not_found
from portfolio_tracker.api.reauth import with_second_factor, wrong_password
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.api.schemas import Email, Language, PersonName, Role, Strict, Username
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import temporary_password
from portfolio_tracker.backup import latest_backup
from portfolio_tracker.db.models import User
from portfolio_tracker.migrations_runner import current_revision, head_revision, list_copies
from portfolio_tracker.repositories import audit, two_factor, users

router = APIRouter(prefix="/admin", tags=["administration"])


class AdminUserOut(BaseModel):
    id: UUID
    username: str
    first_name: str
    last_name: str
    email: str
    role: Role
    two_factor_enabled: bool
    must_change_password: bool
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None

    @classmethod
    def of(cls, user: User, two_factor_enabled: bool) -> "AdminUserOut":
        return cls(
            id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            role=user.role,
            two_factor_enabled=two_factor_enabled,
            must_change_password=user.must_change_password,
            disabled=user.disabled_at is not None,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class CreateUserIn(Strict):
    username: Username
    first_name: PersonName
    last_name: PersonName
    email: Email
    role: Role = "INVESTOR"
    language: Language | None = None  # default: the administrator's language


class StepUpIn(Strict):
    """What the Administrator gives again before acting on a user's access (D-174): the own
    password and, when the Administrator has two-factor authentication, a TOTP code or a
    recovery code (never both). Without it, the password alone."""

    password: str = Field(max_length=4096, repr=False)
    code: str | None = Field(default=None, max_length=32, repr=False)
    recovery_code: str | None = Field(default=None, max_length=64, repr=False)

    @model_validator(mode="after")
    def _at_most_one_factor(self) -> "StepUpIn":
        if self.code is not None and self.recovery_code is not None:
            raise ValueError("give either code or recovery_code")
        return self


_STEP_UP = {
    422: {"description": "CURRENT_PASSWORD_WRONG, TWO_FACTOR_CODE_REQUIRED, TWO_FACTOR_CODE_WRONG"},
    429: {"description": "TOO_MANY_ATTEMPTS"},
}


def _password_refused(request: Request, auth: Auth, body: StepUpIn, at: datetime):
    return wrong_password(
        request, auth.user, body.password, at,
        what="administrator action", action="user.step_up_password_failed",
    )  # fmt: skip


class TemporaryPasswordOut(BaseModel):
    """The temporary password is returned only in this response (D-78)."""

    user: AdminUserOut
    temporary_password: str = Field(repr=False)


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
    enabled = two_factor.enabled_user_ids(db)  # who has it on, never a secret
    return [AdminUserOut.of(user, user.id in enabled) for user in users.list_users(db)]


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
        if users.get_by_email(db, body.email) is not None:
            raise ApiError(409, "EMAIL_TAKEN", "the e-mail address is already in use")
        user = users.add_user(
            db,
            username=body.username,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            role=body.role,
            password_hash=password_hash,
            must_change_password=True,
            language=body.language or auth.user.language,
            password_changed_at=at,
            created_at=at,
        )
        _audit(db, "user.created", auth, user, request, role=user.role)
        return TemporaryPasswordOut(user=_out(db, user), temporary_password=password)


@router.post("/users/{user_id}/reset-password", responses=_STEP_UP)
def reset_password(
    user_id: UUID, body: StepUpIn, auth: ReadAdminAuth, request: Request, at: Now
) -> TemporaryPasswordOut:
    """A new temporary password (shown once); every session of the user is ended. The user's
    two-factor authentication stays (D-174): removing it is `disable-2fa`, on its own.

    The Administrator gives the own password and, with two-factor authentication, a code
    (D-174): a reset lets whoever holds the Administrator's session log in as the user."""
    if (refused := _password_refused(request, auth, body, at)) is not None:
        return refused
    password = temporary_password()
    password_hash = request.app.state.passwords.hash(password)  # outside the write lock

    def work(db: Session) -> TemporaryPasswordOut:
        user = users.get_user(db, user_id) or _missing()
        user.password_hash = password_hash
        user.must_change_password = True
        user.password_changed_at = at
        user.password_reset_at = at  # the notice at the next login (D-165)
        revoked = sessions.revoke_all(db, user.id)
        _audit(db, "user.password_reset", auth, user, request, sessions_revoked=revoked)
        return TemporaryPasswordOut(user=_out(db, user), temporary_password=password)

    return with_second_factor(
        request, auth.user, at, work, code=body.code, recovery_code=body.recovery_code
    )


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
    return _out(db, user)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: UUID, auth: AdminAuth, request: Request, db: DbSession) -> AdminUserOut:
    user = users.get_user(db, user_id) or _missing()
    if user.disabled_at is not None:
        user.disabled_at = None
        _audit(db, "user.enabled", auth, user, request)
    return _out(db, user)


@router.post(
    "/users/{user_id}/disable-2fa",
    responses={**_STEP_UP, 409: {"description": "CANNOT_DISABLE_OWN_2FA"}},
)
def disable_two_factor(
    user_id: UUID, body: StepUpIn, auth: ReadAdminAuth, request: Request, at: Now
) -> AdminUserOut:
    """Remove another user's two-factor authentication, for someone who lost the phone and the
    recovery codes (D-174): the secret and the codes are deleted, every session of the user is
    ended, and the user sees a dated notice from the next login. Never on one's own row: an
    administrator uses Settings, or the container command.

    The Administrator gives the own password and, with two-factor authentication, a code. A
    secret the user was shown but never confirmed is deleted too, and the audit entry says
    so; that alone ends no session and leaves no notice."""
    if user_id == auth.user.id:
        raise ApiError(
            409, "CANNOT_DISABLE_OWN_2FA", "administrators disable their own 2FA in the settings"
        )
    if (refused := _password_refused(request, auth, body, at)) is not None:
        return refused

    def work(db: Session) -> AdminUserOut:
        user = users.get_user(db, user_id) or _missing()
        row = two_factor.get(db, user.id)
        if row is None:
            return _out(db, user)  # nothing to remove, nothing recorded
        pending = row.confirmed_at is None
        two_factor.remove(db, user.id)
        revoked = 0
        if not pending:
            user.two_factor_disabled_at = at
            revoked = sessions.revoke_all(db, user.id)
            request.app.state.login_challenges.drop_user(user.id)  # a login waiting for its code
        _audit(db, "user.two_factor_disabled", auth, user, request, by="administrator",
               was_enabled=not pending, pending_secret_removed=pending,
               sessions_revoked=revoked)  # fmt: skip
        return _out(db, user)

    return with_second_factor(
        request, auth.user, at, work, code=body.code, recovery_code=body.recovery_code
    )


def _out(db, user: User) -> AdminUserOut:
    return AdminUserOut.of(user, two_factor.is_enabled(db, user.id))


def _missing() -> User:
    raise not_found("user")
