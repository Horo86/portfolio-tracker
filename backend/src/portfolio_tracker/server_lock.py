"""The data folder's lock (R-19, D-169 (2)).

The server holds an exclusive `flock` on `<data>/db/.server.lock` from the start of its
lifespan until it stops; the restore command takes the same lock, so a restore never swaps the
database under a running server (and its background loop), and a server never starts during a
restore. Where the file system cannot lock (some network shares), the server warns and goes
on; the restore then relies on its other refusals, and the documentation says to stop the
container first.
"""

import errno
import fcntl
import logging
import os
from pathlib import Path

LOCK_FILE = Path("db") / ".server.lock"

log = logging.getLogger(__name__)


class DataFolderBusy(RuntimeError):
    """Another process holds the data folder's lock."""


def acquire(data_dir: Path, *, purpose: str) -> int | None:
    """Take the lock without waiting. Returns its descriptor (None when the file system cannot
    lock); raises `DataFolderBusy` when another process holds it."""
    path = data_dir / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise DataFolderBusy(
            f"another process is using the data folder {data_dir} (the app, or a restore): "
            f"stop it before the {purpose}"
        ) from None
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOSYS, errno.EINVAL):
            log.warning(
                "the data folder cannot be locked on this file system: stop the app before "
                "a restore",
                extra={"purpose": purpose, "errno": error.errno},
            )
            return None
        raise
    return fd


def release(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
