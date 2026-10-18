"""Container commands for the System Administrator (no web access needed).

`python -m portfolio_tracker reset-password <username>`: the documented recovery for an
Administrator who forgot their password (D-21). Prints a temporary password once; it must be
changed at the next login, and every session of that user is ended.

`python -m portfolio_tracker reset-market-data-config`: replaces the stored engine
configuration with the environment's values (D-132), after showing both and asking for
confirmation on a terminal. It touches no price, listing, FX rate, credential or other data,
and records a listing re-check request that the running app's next run takes up (this
process cannot reach the app's queue). It works while `PT_MARKET_DATA=off`.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import Passwords, temporary_password
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine, db_transaction
from portfolio_tracker.marketdata.base import Role
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.marketdata.routing import ProviderConfigError, RoutingConfig
from portfolio_tracker.migrations_runner import run_migrations
from portfolio_tracker.repositories import audit, market_config, users
from portfolio_tracker.services.market_startup import differences, environment_config


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


def _rows(config: RoutingConfig | None) -> dict[str, str]:
    """The lines shown for one side: the engines on, the order per role, the exchanges."""
    if config is None:
        return dict.fromkeys(["on/off", *(role.value for role in Role), "exchanges"], "(nothing)")
    rows = {"on/off": ", ".join(sorted(config.enabled)) or "(none on)"}
    for role in Role:
        rows[role.value] = ", ".join(config.order(role)) or "(none)"
    rows["exchanges"] = ", ".join(config.exchanges) or "(none)"
    return rows


def reset_market_data_config(
    settings: Settings,
    ask: Callable[[str], str] | None,
    show: Callable[[str], None] = print,
) -> bool:
    """Replace the stored engine configuration with the environment's after confirmation.
    `ask` reads the answer from a terminal; None (no terminal) refuses. Returns whether the
    configuration was replaced."""
    try:
        wanted = environment_config(default_registry(), settings)
    except ProviderConfigError as error:
        raise CliError(f"the environment is not valid: {error}") from None
    engine = create_db_engine(settings.db_path)
    try:
        run_migrations(engine, settings.db_path, settings.pre_migration_backup_dir)
        with db_transaction(engine, write=False) as session:
            stored = market_config.load(session)
        changed = differences(stored, wanted) if stored else ["everything"]
        if not changed:
            show("The stored market data configuration already matches the environment.")
            return False
        show("Market data configuration: stored now | from the environment (* differs)")
        before, after = _rows(stored), _rows(wanted)
        for name in before:
            mark = "*" if before[name] != after[name] else " "
            show(f"{mark} {name:10} {before[name]} | {after[name]}")
        if ask is None:
            raise CliError(
                "no terminal to confirm on: run it in a terminal, e.g. "
                "docker exec -it portfolio-tracker python -m portfolio_tracker "
                "reset-market-data-config"
            )
        answer = ask(
            "Replace the stored values with the environment's? Prices, listings, FX rates and "
            "credentials are not touched; the app re-checks the listings at its next run. "
            "[y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            show("Nothing changed.")
            return False
        with db_transaction(engine, write=True) as session:
            if market_config.load(session) != stored:
                raise CliError("the stored configuration changed meanwhile: run it again")
            market_config.save(session, wanted)
            market_config.request_resolve(session, datetime.now(UTC))
            audit.record(
                session,
                "market_data.config_reset",
                actor_id=None,
                entity_type="market_data_config",
                after={"by": "container command", "changed": changed},
            )
        show("Done. The app re-checks the listings at its next run; no restart is needed.")
        return True
    finally:
        engine.dispose()
