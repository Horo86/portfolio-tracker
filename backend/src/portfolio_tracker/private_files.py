"""The database and its copies are for the app's user alone: folders `0700`, files `0600`.

They hold every user's data, the password hashes and the two-factor secrets (D-33: nothing
is encrypted at rest except the provider credentials). SQLite itself would create a database
with `0644` less the umask, readable by every other user of the host.

New files are created private. What exists already (an installation of an earlier version)
is tightened at each start, but only when this process owns it and only by removing the
group's and the others' access: the owner's own bits never change, so the app cannot lock
itself out. A path owned by someone else (an installation that works through group
permissions), or a file system where modes cannot be changed (some network shares, ACL-only
volumes), is left as it is, silently: the app goes on exactly as before.
"""

import logging
import os
import stat
from pathlib import Path

PRIVATE_FILE = 0o600
_OTHERS = 0o077

log = logging.getLogger(__name__)


def restrict(path: Path, *, new: bool = False) -> None:
    """Remove the group's and the others' access to `path`, if it exists and is ours. Logged
    once for something that existed already, not for what was created just now (`new`)."""
    try:
        status = path.stat()
        mode = stat.S_IMODE(status.st_mode)
        if not mode & _OTHERS or status.st_uid != os.geteuid():
            return
        path.chmod(mode & ~_OTHERS)
    except OSError:
        return
    if not new:
        log.info("access for other users removed", extra={"path": str(path)})


def private_folder(path: Path) -> None:
    """Create `path` if needed (parents as usual) and keep it for the app's user."""
    new = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    restrict(path, new=new)


def create_private_file(path: Path) -> None:
    """A new empty file with mode `0600`, never readable by others while it is filled; an
    existing one is tightened."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_FILE))
    except FileExistsError:
        restrict(path)


def restrict_database(db_path: Path) -> None:
    """The database file and what SQLite keeps beside it. SQLite gives a new `-wal`, `-shm`
    or `-journal` the mode of the database file, so once that is private they are too."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        restrict(db_path.with_name(db_path.name + suffix))
