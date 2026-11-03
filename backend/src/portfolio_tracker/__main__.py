"""Entry point.

python -m portfolio_tracker                           run the server (PT_* variables)
python -m portfolio_tracker reset-password <username> print a temporary password once
python -m portfolio_tracker reset-market-data-config  engine configuration from the environment
"""

import argparse
import sys

import uvicorn
from pydantic import ValidationError

from portfolio_tracker.cli import CliError, reset_market_data_config, reset_password
from portfolio_tracker.config import Settings
from portfolio_tracker.logging_setup import setup_logging


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
    if args.command == "reset-password":
        try:
            password = reset_password(settings, args.username)
        except CliError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Temporary password for {args.username}: {password}")
        print("It is shown only now; it must be changed at the next login.")
        return 0
    if args.command == "reset-market-data-config":
        try:
            reset_market_data_config(settings, input if sys.stdin.isatty() else None)
        except CliError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
