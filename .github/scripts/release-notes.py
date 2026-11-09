"""The release notes of one version, from CHANGELOG.md (D-173 (3)).

CHANGELOG.md is kept to a small format, which the app's build (frontend/scripts/
release-notes.mjs) and this script both read: `# Changelog`, an introduction, then `## Unreleased`
and one `## X.Y.Z` (or `X.Y.Z-rc.N`, `-alpha.N`, `-beta.N`) section per version, newest first;
inside a section only `### Added|Changed|Fixed|Removed|Security` headings and `- ` items of one
line each. Anything else is refused, so nothing in the file needs HTML to be shown.

Used by release.yml's verify job: the section of the version being released must exist and say
something, and is written out for the GitHub release (the maintainer creates it, D-167).

Usage: release-notes.py VERSION [--output FILE]   (the tag without "v"; exit 0 = found, 1 = not)
"""

import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc)\.(\d+))?")
GROUPS = ("Added", "Changed", "Fixed", "Removed", "Security")
UNRELEASED = "Unreleased"


class ChangelogError(Exception):
    pass


def order(version: str) -> tuple:
    """Newest is largest: a pre-release comes before its plain version."""
    match = VERSION.fullmatch(version)
    if not match:
        raise ChangelogError(f"{version!r} is not a version (X.Y.Z or X.Y.Z-rc.N)")
    major, minor, patch, kind, number = match.groups()
    rank = {"alpha": 0, "beta": 1, "rc": 2, None: 3}[kind]
    return (int(major), int(minor), int(patch), rank, int(number or 0))


def parse(text: str) -> dict[str, list[tuple[str, list[str]]]]:
    """{section: [(group, [item, …]), …]} in file order, after checking the format."""
    lines = text.splitlines()
    if not lines or lines[0] != "# Changelog":
        raise ChangelogError("the first line must be '# Changelog'")
    sections: dict[str, list[tuple[str, list[str]]]] = {}
    current: list[tuple[str, list[str]]] | None = None
    for number, line in enumerate(lines[1:], start=2):
        if line.startswith("## "):
            name = line[3:]
            if name != UNRELEASED:
                order(name)
            if name in sections:
                raise ChangelogError(f"line {number}: {name} appears twice")
            current = sections.setdefault(name, [])
        elif current is None:
            if line.startswith("#") or line.startswith("- "):
                raise ChangelogError(f"line {number}: only text before the first section")
        elif not line.strip():
            continue
        elif line.startswith("### "):
            if line[4:] not in GROUPS:
                raise ChangelogError(f"line {number}: a group is one of {', '.join(GROUPS)}")
            current.append((line[4:], []))
        elif line.startswith("- ") and line[2:].strip():
            if not current:
                raise ChangelogError(f"line {number}: an item needs a ### group above it")
            current[-1][1].append(line[2:])
        else:
            raise ChangelogError(f"line {number}: only ### groups and one-line '- ' items here")
    names = list(sections)
    if not names or names[0] != UNRELEASED:
        raise ChangelogError("the first section must be '## Unreleased'")
    versions = names[1:]
    if versions != sorted(versions, key=order, reverse=True):
        raise ChangelogError("the versions must be newest first")
    return sections


def section(version: str, text: str) -> str:
    """The section of `version` as Markdown, groups and items, or ChangelogError."""
    groups = parse(text).get(version)
    if not groups or not any(items for _, items in groups):
        raise ChangelogError(f"CHANGELOG.md has no notes for {version}")
    return (
        "\n\n".join(
            f"### {group}\n" + "\n".join(f"- {item}" for item in items)
            for group, items in groups
            if items
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="the version being released, without the v")
    parser.add_argument("--output", type=Path, help="write the notes here instead of stdout")
    args = parser.parse_args(argv)
    try:
        notes = section(args.version, CHANGELOG.read_text("utf-8"))
    except (ChangelogError, OSError) as error:
        print(f"::error::{error}")
        return 1
    if args.output:
        args.output.write_text(notes, "utf-8")
    else:
        print(notes, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
