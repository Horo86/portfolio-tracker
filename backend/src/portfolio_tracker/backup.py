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
    files: tuple[Path, ...]  # relative to the data directory


BACKUP_EXCLUDED = BackupExclusions(tables=("provider_credentials",), files=(KEY_FILE,))
