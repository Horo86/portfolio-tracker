"""The security settings for the Administrator (D-180, D-181): how long sessions last and how
many failed logins lead to a block.

Administrators only (404 for anyone else, D-92). Reading needs nothing more; every save asks
the Administrator's own password and, with two-factor authentication, a code, whatever it
changes: the settings decide how hard the installation is to break into.
"""

from dataclasses import asdict, fields
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import AdminAuth, Now, ReadAdminAuth
from portfolio_tracker.api.errors import ApiError
from portfolio_tracker.api.reauth import (
    STEP_UP,
    StepUpIn,
    password_refused,
    with_second_factor,
)
from portfolio_tracker.api.routes.auth import client_ip
from portfolio_tracker.auth.security_settings import (
    ADDRESS_UNDER_ACCOUNT,
    BOUNDS,
    IDLE_OVER_ABSOLUTE,
    SecuritySettings,
    from_environment,
    problems,
)
from portfolio_tracker.repositories import audit
from portfolio_tracker.services.security_settings import Effective, change, effective, refresh

router = APIRouter(prefix="/admin/security-settings", tags=["administration"])

_FIELDS = tuple(field.name for field in fields(SecuritySettings))


class SettingsOut(BaseModel):
    session_idle_days: int
    session_absolute_days: int
    login_account_failures: int
    login_address_failures: int
    block_first_minutes: int
    block_max_minutes: int


class BoundOut(BaseModel):
    min: int
    max: int


class SecuritySettingsOut(BaseModel):
    """The values in force, where they come from (`environment` until the first save, then
    `database`), when they were saved, and the inclusive bounds of each."""

    source: Literal["environment", "database"]
    updated_at: datetime | None
    settings: SettingsOut
    bounds: dict[str, BoundOut]

    @classmethod
    def of(cls, now: Effective) -> "SecuritySettingsOut":
        return cls(
            source=now.source,
            updated_at=now.updated_at,
            settings=SettingsOut(**{name: getattr(now.values, name) for name in _FIELDS}),
            bounds={name: BoundOut(min=low, max=high) for name, (low, high) in BOUNDS.items()},
        )


class SecuritySettingsSavedOut(SecuritySettingsOut):
    sessions_shortened: int  # other open sessions capped by a shorter absolute duration


class SecuritySettingsIn(StepUpIn):
    """Every value (whole numbers only: `"7"`, `7.5` and `true` are refused), and the
    Administrator's own password with, under two-factor authentication, a code."""

    session_idle_days: StrictInt
    session_absolute_days: StrictInt
    login_account_failures: StrictInt
    login_address_failures: StrictInt
    block_first_minutes: StrictInt
    block_max_minutes: StrictInt


_RESPONSES = {
    **STEP_UP,
    422: {
        "description": "REQUEST_INVALID, SECURITY_SETTINGS_INVALID, CURRENT_PASSWORD_WRONG, "
        "TWO_FACTOR_CODE_REQUIRED, TWO_FACTOR_CODE_WRONG"
    },
}


def _invalid(values: SecuritySettings) -> ApiError | None:
    """422 `SECURITY_SETTINGS_INVALID` with each field, the rule it breaks and the range it
    must be in (for a cross rule, the range the other value allows)."""
    found = problems(asdict(values))
    if not found:
        return None
    errors = []
    for problem in found:
        low, high = BOUNDS[problem.field]
        if problem.rule == IDLE_OVER_ABSOLUTE:
            high = values.session_absolute_days
        elif problem.rule == ADDRESS_UNDER_ACCOUNT:
            low = 2 * values.login_account_failures
        errors.append({"field": problem.field, "rule": problem.rule, "min": low, "max": high})
    return ApiError(422, "SECURITY_SETTINGS_INVALID", "settings out of range", errors=errors)


@router.get("")
def read_security_settings(auth: AdminAuth, request: Request) -> SecuritySettingsOut:
    return SecuritySettingsOut.of(request.app.state.security)


@router.put("", responses=_RESPONSES)
def save_security_settings(
    body: SecuritySettingsIn, auth: ReadAdminAuth, request: Request, at: Now
) -> SecuritySettingsSavedOut:
    """Store every value; they apply at once, without a restart (D-180).

    The order of the refusals (D-181): role, temporary password, enrolment (the
    dependencies), body shape, bounds and cross rules, password, code. The values are checked
    before the password, so a form with a wrong value never counts as a guess nor uses a
    code. A save of the values already stored writes and records nothing."""
    new = SecuritySettings(**{name: getattr(body, name) for name in _FIELDS})
    if (invalid := _invalid(new)) is not None:
        raise invalid
    if (refused := password_refused(request, auth, body, at)) is not None:
        return refused
    environment, _ = from_environment(request.app.state.settings)

    def work(db: Session) -> int:
        now = effective(db, environment)  # read again inside the write transaction
        if now.source == "database" and now.values == new:
            return 0
        shortened = change(db, auth.session.id, now.values, new, at)
        audit.record(
            db,
            "security.settings_changed",
            actor_id=auth.user.id,
            entity_type="security_settings",
            before={**asdict(now.values), "source": now.source},
            after={**asdict(new), "sessions_shortened": shortened},
            ip=client_ip(request),
        )
        return shortened

    result = with_second_factor(
        request, auth.user, at, work, code=body.code, recovery_code=body.recovery_code
    )
    if isinstance(result, JSONResponse):
        return result
    now = refresh(request.app)  # committed: the new values apply from the next request
    return SecuritySettingsSavedOut(
        **SecuritySettingsOut.of(now).model_dump(), sessions_shortened=result
    )
