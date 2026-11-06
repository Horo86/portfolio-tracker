"""The version's preview flag and the image's build date (D-167)."""

import re
from datetime import UTC, datetime

_PLAIN = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def is_preview(version: str) -> bool:
    """A preview is a version that is not plain X.Y.Z (`1.0.0-rc.1`, the `0.0.0` of an
    uninstalled tree is plain) or whose major is 0, so the label goes away by itself at 1.0.0."""
    match = _PLAIN.fullmatch(version)
    return match is None or int(match[1]) == 0


def build_date_text(build_date: datetime | None) -> str | None:
    """The build date in UTC, `2026-11-06T20:25:16Z`; None in a development run."""
    if build_date is None:
        return None
    return build_date.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
