"""What the backup archive (M7, ADR-0008) must leave out (D-21c, D-124).

Provider credentials are never part of a backup: neither their table nor the key that
decrypts them. After a restore, the administrator enters the credentials again.
"""

from dataclasses import dataclass
from pathlib import Path

from portfolio_tracker.credentials import KEY_FILE


@dataclass(frozen=True)
class BackupExclusions:
    tables: tuple[str, ...]  # emptied in the database copy
    paths: tuple[Path, ...]  # files or whole folders, relative to the data directory


# The whole secrets folder: the key file and any temporary copy of it (review L5).
BACKUP_EXCLUDED = BackupExclusions(tables=("provider_credentials",), paths=(KEY_FILE.parent,))
