"""The security settings in force and their change (D-180, D-181).

"The environment seeds, the database wins once saved": with no stored row the values are the
environment's (`from_environment`, read at every start); the first save stores every value and
from then on the stored row applies. The values in force live in `app.state.security`; the
session and throttle policies derived from them are replaced by `refresh`, so a save takes
effect without a restart.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.security_settings import (
    ENVIRONMENT_FIELDS,
    SecuritySettings,
    from_environment,
    inside,
    session_policy,
    throttle_policy,
)
from portfolio_tracker.db import db_transaction
from portfolio_tracker.db.models import UserSession
from portfolio_tracker.repositories import security_settings as stored

log = logging.getLogger(__name__)

Source = Literal["environment", "database"]

_refresh_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class Effective:
    values: SecuritySettings
    source: Source
    updated_at: datetime | None  # when the stored row was saved; None from the environment


def effective(db: Session, environment: SecuritySettings) -> Effective:
    """The values in force: the stored row brought inside the bounds (a warning per field
    changed, never a refusal: D-180), or `environment` while nothing was saved."""
    found = stored.load(db)
    if found is None:
        return Effective(environment, "environment", None)
    values, at = found
    values, changed = inside(values)
    for name in changed:
        log.warning(
            "stored security setting outside its range, adjusted",
            extra={"setting": name, "value_used": getattr(values, name)},
        )
    return Effective(values, "database", at)


def refresh(app) -> Effective:
    """Read the values in force and make them the app's: `app.state.security`, the session
    policy and the throttle policy. Called at start after the migrations and after every
    committed save. The throttle keeps its counts and running blocks (`set_policy`)."""
    with _refresh_lock:
        environment, _ = from_environment(app.state.settings)
        with db_transaction(app.state.engine, write=False) as db:
            now = effective(db, environment)
        app.state.session_policy = session_policy(now.values)
        app.state.login_throttle.set_policy(throttle_policy(now.values))
        app.state.security = now
        return now


def environment_differences(app) -> list[str]:
    """The environment variables whose value differs from the stored row in force: said in the
    start log, since the stored value wins (D-180). Empty while nothing was saved."""
    now: Effective = app.state.security
    if now.source != "database":
        return []
    environment, _ = from_environment(app.state.settings)
    return [
        variable
        for variable, name in ENVIRONMENT_FIELDS.items()
        if getattr(environment, name) != getattr(now.values, name)
    ]


def change(
    db: Session,
    acting_session_id: UUID,
    old: SecuritySettings,
    new: SecuritySettings,
    at: datetime,
) -> int:
    """Store `new` in place of `old` (the values in force), in the caller's write transaction,
    and apply the durations to the open sessions (D-180 (5)). Returns how many sessions were
    shortened.

    First the sessions `old` already rejects are deleted, so a longer idle time never revives
    one. Idle then applies at once both ways through the session policy. A longer absolute
    duration applies to new sessions only (each session keeps its end date). A shorter one caps
    every other open session at its creation plus the new duration; the acting session keeps
    its end date, so whoever saves is not logged out by the save."""
    sessions.delete_expired(db, at, session_policy(old))
    shortened = 0
    if new.session_absolute_days < old.session_absolute_days:
        cap = timedelta(days=new.session_absolute_days)
        others = db.scalars(select(UserSession).where(UserSession.id != acting_session_id))
        for row in others:
            limit = row.created_at + cap
            if limit < row.expires_at:
                row.expires_at = limit
                shortened += 1
    stored.save(db, new, at)
    return shortened
