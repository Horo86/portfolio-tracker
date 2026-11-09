"""Entry point.

python -m portfolio_tracker                           run the server (PT_* variables)
python -m portfolio_tracker reset-password <username> print a temporary password once
python -m portfolio_tracker reset-market-data-config  engine configuration from the environment
python -m portfolio_tracker backup [--output DIR]     write a backup archive (D-169)
python -m portfolio_tracker restore <archive>         restore it, with the app stopped (D-169)
"""

import argparse
import sys
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from portfolio_tracker.backup import BackupError, make_backup
from portfolio_tracker.cli import CliError, reset_market_data_config, reset_password
from portfolio_tracker.config import Settings
from portfolio_tracker.logging_setup import setup_logging
from portfolio_tracker.migrations_runner import MigrationRefused, preflight, refuse_preview
from portfolio_tracker.restore import RestoreError, restore

BACKUP_NOTICE = (
    "It is NOT encrypted: anyone who has the file can read all the data of this instance.\n"
    "Copy it away from this server, and encrypt it by hand if you keep it elsewhere\n"
    "(docs/backup-format.md). Provider credentials and sessions are not in it: after a\n"
    "restore, enter the credentials again and log in again."
)

RESTORE_NOTICE = (
    "Start the app: it upgrades the database if needed, with a copy taken first.\n"
    "Everyone logs in with their own password; the administrator enters the provider\n"
    "credentials again, if any were set."
)


def serve(settings: Settings) -> None:
    uvicorn.run(
        "portfolio_tracker.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        workers=1,
        # Uvicorn's own forwarded-header handling stays off: PT_TRUSTED_PROXIES is applied by
        # the app's ForwardedHeaders middleware, the one place that decides (D-164).
        proxy_headers=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m portfolio_tracker")
    commands = parser.add_subparsers(dest="command")
    reset = commands.add_parser(
        "reset-password", help="set a temporary password for a user (shown once)"
    )
    reset.add_argument("username")
    commands.add_parser(
        "reset-market-data-config",
        help="replace the stored market data engine configuration with the environment's",
    )
    backup = commands.add_parser("backup", help="write a backup archive of the instance")
    backup.add_argument(
        "--output", type=Path, help="the folder for the archive (default: <data>/backups/manual)"
    )
    restoring = commands.add_parser(
        "restore", help="restore a backup archive into an instance without users (app stopped)"
    )
    restoring.add_argument("archive", type=Path, help="the backup archive (.zip)")
    args = parser.parse_args(argv)

    try:
        settings = Settings()
    except ValidationError as exc:  # fail fast, with the variable's name and no traceback
        for error in exc.errors():
            name = "PT_" + "_".join(str(part) for part in error["loc"]).upper()
            message = error["msg"].removeprefix("Value error, ")
            print(f"error: invalid configuration {name}: {message}", file=sys.stderr)
        return 2
    setup_logging(settings.log_level)
    try:  # before any command writes anything in the folder: no lock, key or copy (D-173)
        refuse_preview(settings.db_path)
    except MigrationRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "reset-password":
        try:
            password = reset_password(settings, args.username)
        except (CliError, MigrationRefused) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Temporary password for {args.username}: {password}")
        print("It is shown only now; it must be changed at the next login.")
        return 0
    if args.command == "reset-market-data-config":
        try:
            reset_market_data_config(settings, input if sys.stdin.isatty() else None)
        except (CliError, MigrationRefused) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "backup":
        try:
            result = make_backup(settings.db_path, args.output or settings.manual_backup_dir)
        except (BackupError, MigrationRefused) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"Backup written: {result.path} ({result.size} bytes, schema {result.schema_revision})"
        )
        print(BACKUP_NOTICE)
        return 0
    if args.command == "restore":
        try:
            result = restore(args.archive, settings.data_dir)
        except RestoreError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(
            f"Restored: {result.db_path} (schema {result.schema_revision}, archive of version "
            f"{result.app_version}, taken {result.created_at})"
        )
        print(RESTORE_NOTICE)
        return 0
    try:  # before uvicorn, so the message comes without a traceback (R-18, D-168)
        preflight(settings.db_path, settings.pre_migration_backup_dir)
    except MigrationRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
