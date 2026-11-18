"""The security settings an Administrator will be able to change (D-177): the two session
durations and the lockout thresholds, with their bounds, their cross rules and the way they
become the session and throttle policies. Pure: no database, no web import.

Until an Administrator saves them, the values come from the environment (the two durations)
or are the built-in defaults (the four lockout values); from the first save the stored values
apply (D-180, `services/security_settings.py`).
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import timedelta
from typing import Final

from portfolio_tracker.auth.sessions import SessionPolicy
from portfolio_tracker.auth.throttle import (
    IP,
    PASSWORD_CHANGE,
    TOTP,
    USER,
    ThrottlePolicy,
)
from portfolio_tracker.config import Settings

# Rules a value can break (each problem carries the field and the rule).
RANGE: Final = "RANGE"
IDLE_OVER_ABSOLUTE: Final = "IDLE_OVER_ABSOLUTE"
ADDRESS_UNDER_ACCOUNT: Final = "ADDRESS_UNDER_ACCOUNT"
BOOLEAN: Final = "BOOLEAN"  # the obligation is true or false, nothing else

MIN_FORGET_AFTER = timedelta(minutes=30)
TOTP_FAILURES = 5  # the code guesses keep their threshold (D-174 (6))


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    session_idle_days: int = 7
    session_absolute_days: int = 30
    login_account_failures: int = 5
    login_address_failures: int = 30
    block_first_minutes: int = 1
    block_max_minutes: int = 15
    # Two-factor authentication for every user, administrators too (D-183). It only adds an
    # obligation: `PT_REQUIRE_2FA` keeps deciding for administrators when it is false.
    require_2fa_all_users: bool = False


DEFAULTS: Final = SecuritySettings()

# Inclusive bounds. The first block never exceeds the maximum: 15 tops one range and
# bottoms the other.
BOUNDS: Final[Mapping[str, tuple[int, int]]] = {
    "session_idle_days": (1, 30),
    "session_absolute_days": (1, 90),
    "login_account_failures": (3, 20),
    "login_address_failures": (10, 200),
    "block_first_minutes": (1, 15),
    "block_max_minutes": (15, 120),
}

# The environment variables that are the initial values of two settings.
ENVIRONMENT_FIELDS: Final[Mapping[str, str]] = {
    "PT_SESSION_IDLE_DAYS": "session_idle_days",
    "PT_SESSION_ABSOLUTE_DAYS": "session_absolute_days",
}


@dataclass(frozen=True, slots=True)
class Problem:
    field: str
    rule: str


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def problems(values: Mapping[str, object]) -> list[Problem]:
    """Every rule the values break: a field missing, not a whole number or out of its range
    (`RANGE`), then the two cross rules for the fields that are themselves valid. The
    obligation of D-183, when given, must be a boolean (`BOOLEAN`); missing, it is false."""
    found: list[Problem] = []
    good: dict[str, int] = {}
    for name, (low, high) in BOUNDS.items():
        value = values.get(name)
        if _whole(value) and low <= value <= high:  # type: ignore[operator]
            good[name] = value  # type: ignore[assignment]
        else:
            found.append(Problem(name, RANGE))
    idle, absolute = good.get("session_idle_days"), good.get("session_absolute_days")
    if idle is not None and absolute is not None and idle > absolute:
        found.append(Problem("session_idle_days", IDLE_OVER_ABSOLUTE))
    account, address = good.get("login_account_failures"), good.get("login_address_failures")
    if account is not None and address is not None and address < 2 * account:
        found.append(Problem("login_address_failures", ADDRESS_UNDER_ACCOUNT))
    if "require_2fa_all_users" in values and not isinstance(values["require_2fa_all_users"], bool):
        found.append(Problem("require_2fa_all_users", BOOLEAN))
    return found


def _inside(name: str, value: int) -> int:
    low, high = BOUNDS[name]
    return min(max(value, low), high)


def inside(values: SecuritySettings) -> tuple[SecuritySettings, list[str]]:
    """`values` brought inside the bounds and the cross rules, and the names of the fields
    that were changed: for a stored row edited by hand or restored from another version
    (D-180). Each value is moved to its nearest bound; then idle is brought down to absolute
    and the address threshold up to twice the account one (at most 40, inside its range)."""
    used = {f.name: getattr(values, f.name) for f in fields(values)}
    for name in BOUNDS:
        used[name] = _inside(name, used[name])
    if used["session_idle_days"] > used["session_absolute_days"]:
        used["session_idle_days"] = used["session_absolute_days"]
    if used["login_address_failures"] < 2 * used["login_account_failures"]:
        used["login_address_failures"] = 2 * used["login_account_failures"]
    changed = [name for name in BOUNDS if used[name] != getattr(values, name)]
    return SecuritySettings(**used), changed


def from_environment(settings: Settings) -> tuple[SecuritySettings, list[str]]:
    """The values at start: the two durations from the environment, the four lockout values
    the defaults, each brought inside its bounds and idle brought down to absolute. Also the
    names of the variables that were adjusted; it never refuses (the start never stops)."""
    wanted = {
        "PT_SESSION_IDLE_DAYS": settings.session_idle_days,
        "PT_SESSION_ABSOLUTE_DAYS": settings.session_absolute_days,
    }
    used: dict[str, int] = {}
    adjusted: list[str] = []
    for variable, value in wanted.items():
        name = ENVIRONMENT_FIELDS[variable]
        used[name] = _inside(name, value)
    if used["session_idle_days"] > used["session_absolute_days"]:
        used["session_idle_days"] = used["session_absolute_days"]
    for variable, name in ENVIRONMENT_FIELDS.items():
        if used[name] != wanted[variable]:
            adjusted.append(variable)
    values = SecuritySettings(
        **{**{f.name: getattr(DEFAULTS, f.name) for f in fields(DEFAULTS)}, **used}
    )
    return values, adjusted


def session_policy(values: SecuritySettings) -> SessionPolicy:
    return SessionPolicy(
        idle=timedelta(days=values.session_idle_days),
        absolute=timedelta(days=values.session_absolute_days),
    )


def throttle_policy(values: SecuritySettings) -> ThrottlePolicy:
    """The lockout policy. The count's memory follows the longest block: with the 30 minutes
    of a 15-minute cap, a longer block would end with its count erased and the doubling would
    start again, so the cap would slow nothing. Derived, never a setting; the code guesses
    keep their 24 hours. The two block lengths are global, so they apply to codes too."""
    longest = timedelta(minutes=values.block_max_minutes)
    return ThrottlePolicy(
        free_failures={
            USER: values.login_account_failures,
            PASSWORD_CHANGE: values.login_account_failures,
            IP: values.login_address_failures,
            TOTP: TOTP_FAILURES,
        },
        first_block=timedelta(minutes=values.block_first_minutes),
        max_block=longest,
        forget_after=max(MIN_FORGET_AFTER, 2 * longest),
    )
