"""Container commands for the System Administrator (no web access needed).

`python -m portfolio_tracker reset-password <username>`: the documented recovery for an
Administrator who forgot their password (D-21). Prints a temporary password once; it must be
changed at the next login, and every session of that user is ended.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import Passwords, temporary_password
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import audit, users


class CliError(Exception):
    pass


def reset_password(settings: Settings, username: str, *, passwords: Passwords | None = None) -> str:
    """Set a temporary password for `username`; returns it (shown once by the caller)."""
    engine = create_db_engine(settings.db_path)
    try:
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
        with Session(engine) as db, db.begin():
            user = users.get_by_username(db, username)
            if user is None:
                raise CliError(f"no user named {username!r}")
            password = temporary_password()
            user.password_hash = (passwords or Passwords()).hash(password)
            user.must_change_password = True
            user.password_changed_at = datetime.now(UTC)
            revoked = sessions.revoke_all(db, user.id)
            audit.record(
                db,
                "user.password_reset",
                actor_id=None,
                entity_type="user",
                entity_id=user.id,
                after={"by": "container command", "sessions_revoked": revoked},
            )
            return password
    finally:
        engine.dispose()
