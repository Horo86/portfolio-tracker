"""Entry point: python -m portfolio_tracker (host, port and log level from PT_* variables)."""

import uvicorn

from portfolio_tracker.config import Settings
from portfolio_tracker.logging_setup import setup_logging


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level)
    uvicorn.run(
        "portfolio_tracker.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        workers=1,
    )


if __name__ == "__main__":
    main()
