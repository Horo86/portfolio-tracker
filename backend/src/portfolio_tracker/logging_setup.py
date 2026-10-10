"""Structured JSON logs on standard output."""

import logging
import sys

from pythonjsonlogger.json import JsonFormatter


class _DropTerminalColors(logging.Filter):
    """Uvicorn attaches an ANSI-coloured copy of its messages; JSON logs do not need it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.__dict__.pop("color_message", None)
        return True


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_DropTerminalColors())
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "time", "levelname": "level", "name": "logger"},
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "alembic"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
