"""Entry point.

python -m portfolio_tracker                           run the server (PT_* variables)
python -m portfolio_tracker reset-password <username> print a temporary password once
"""

import argparse
import sys

import uvicorn

from portfolio_tracker.cli import CliError, reset_password
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
        # Forwarded headers are not trusted until the trusted-proxy setting exists (M6):
        # the client IP is always the direct peer (D-82).
        proxy_headers=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m portfolio_tracker")
    commands = parser.add_subparsers(dest="command")
    reset = commands.add_parser(
        "reset-password", help="set a temporary password for a user (shown once)"
    )
    reset.add_argument("username")
    args = parser.parse_args(argv)

    settings = Settings()
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
    serve(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
