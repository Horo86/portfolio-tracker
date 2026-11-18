"""The stored security settings (D-180): one row, written at the first save only.

Instance-wide: no owner. Read at every start and after every save (`services/security_settings`).
"""

from dataclasses import asdict
from datetime import datetime

from sqlalchemy.orm import Session

from portfolio_tracker.auth.security_settings import SecuritySettings
from portfolio_tracker.db.models import SecuritySettingsRow

_FIELDS = tuple(asdict(SecuritySettings()))


def load(session: Session) -> tuple[SecuritySettings, datetime] | None:
    """The stored values as they are (not brought inside the bounds) and when they were
    saved; None while nothing was ever saved."""
    row = session.get(SecuritySettingsRow, 1)
    if row is None:
        return None
    return SecuritySettings(**{name: getattr(row, name) for name in _FIELDS}), row.updated_at


def save(session: Session, values: SecuritySettings, at: datetime) -> None:
    """Store every value; the first save creates the row."""
    row = session.get(SecuritySettingsRow, 1)
    if row is None:
        row = SecuritySettingsRow(id=1)
        session.add(row)
    for name in _FIELDS:
        setattr(row, name, getattr(values, name))
    row.updated_at = at
    session.flush()
