"""Refuse a stable release while an earlier stable release has no upgrade fixture (D-168 (7)).

Every plain vX.Y.Z tag older than the version being released must be listed in
backend/tests/fixtures/upgrade/index.json, with a fixture file of its own or with
"same_as": "<earlier version>" pointing to one (a patch release that changed neither the schema
nor the shape of the data). A pre-release (-alpha.N, -beta.N, -rc.N) is not gated: it is only
tried on a test installation. Used by release.yml's verify job, on a checkout with all tags
(fetch-depth: 0). It fails closed: when the tag being released is not among the tags read
(none fetched, or an empty list), it refuses instead of finding "no earlier release".

Usage: upgrade-fixture-gate.py VERSION   (the tag without "v"; exit 0 = go, 1 = refused)
"""

import json
import re
import subprocess
import sys
from pathlib import Path

PLAIN = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
INDEX = Path("backend/tests/fixtures/upgrade/index.json")


def plain(version: str) -> tuple[int, int, int] | None:
    match = PLAIN.fullmatch(version)
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def covered(index: dict) -> set[str]:
    """The versions whose upgrade is tested: a fixture file, or same_as one that has it."""
    entries = {entry["version"]: entry for entry in index.get("fixtures", [])}
    return {
        version
        for version, entry in entries.items()
        if "file" in entry or "file" in entries.get(entry.get("same_as", ""), {})
    }


class TagsNotFetched(Exception):
    pass


def missing(version: str, tags: list[str], index: dict) -> list[str] | None:
    """The earlier stable versions without a fixture, or None when `version` is not gated.
    Raises `TagsNotFetched` when the tag of `version` itself is not in `tags`."""
    current = plain(version)
    if current is None:
        return None  # a pre-release
    if f"v{version}" not in tags:
        raise TagsNotFetched(
            f"the tag v{version} is not among the {len(tags)} tag(s) read: the tags were not "
            "fetched (the checkout needs fetch-depth: 0), so the earlier releases are unknown"
        )
    earlier = sorted(
        (number, tag.removeprefix("v"))
        for tag in tags
        if (number := plain(tag)) is not None and number < current
    )
    have = covered(index)
    return [name for _, name in earlier if name not in have]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 1
    version = argv[0]
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, capture_output=True, text=True
    ).stdout.split()
    index = json.loads(INDEX.read_text("utf-8")) if INDEX.exists() else {}
    try:
        gaps = missing(version, tags, index)
    except TagsNotFetched as error:
        print(f"::error::{error}")
        return 1
    if gaps is None:
        print(f"{version} is a pre-release: the upgrade fixture gate does not apply")
        return 0
    if gaps:
        print(
            f"::error::earlier releases without an upgrade fixture in {INDEX}: "
            f"{', '.join(gaps)}. Add one with scripts/make_upgrade_fixture.py on that tag, or "
            '{"version": ..., "same_as": ...} when it changed neither the schema nor the data.'
        )
        return 1
    print(f"every stable release before {version} has an upgrade fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
