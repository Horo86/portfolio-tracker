"""Print the image aliases that the release of VERSION moves (D-170 (4)).

A plain X.Y.Z gives:
- X.Y when it is the highest plain vX.Y.* tag;
- X when it is the highest plain vX.* tag, and only from major 1 (D-167: none for 0.x);
- latest when it is the highest plain tag of all, and only from major 1.
A pre-release (-alpha.N, -beta.N, -rc.N) moves no alias: it is published only as itself.

release.yml runs it in the publish job before logging in to the registry, on a checkout with
all tags, so a re-run of an older release sees the newer tags and moves nothing back. It fails
closed: when the tag being released is not among the tags read (they were not fetched), or the
version is not one the workflow accepts, it exits 1 and the job stops before any push.

Usage: image-aliases.py VERSION   (the tag without "v"; prints the aliases on one line,
space-separated, possibly empty; exit 0 = go, 1 = refused)
"""

import re
import subprocess
import sys

RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)(-(alpha|beta|rc)\.\d+)?")
PLAIN_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


class Refused(Exception):
    pass


def aliases(version: str, tags: list[str]) -> list[str]:
    """The aliases VERSION moves, given every tag of the repository. Raises `Refused`."""
    match = RELEASE.fullmatch(version)
    if match is None:
        raise Refused(f"{version!r} is not X.Y.Z or X.Y.Z-(alpha|beta|rc).N")
    if f"v{version}" not in tags:
        raise Refused(
            f"the tag v{version} is not among the {len(tags)} tag(s) read: the tags were not "
            "fetched (the checkout needs fetch-depth: 0), so the highest version is unknown"
        )
    if match[4]:
        return []  # a pre-release
    current = (int(match[1]), int(match[2]), int(match[3]))
    plain = [
        (int(found[1]), int(found[2]), int(found[3]))
        for tag in tags
        if (found := PLAIN_TAG.fullmatch(tag))
    ]
    major, minor, _ = current
    result = []
    if current == max(v for v in plain if v[:2] == (major, minor)):
        result.append(f"{major}.{minor}")
    if major >= 1:
        if current == max(v for v in plain if v[0] == major):
            result.append(f"{major}")
        if current == max(plain):
            result.append("latest")
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-2], file=sys.stderr)
        return 1
    version = argv[0]
    tags = subprocess.run(
        ["git", "tag", "--list", "v*"], check=True, capture_output=True, text=True
    ).stdout.split()
    try:
        found = aliases(version, tags)
    except Refused as error:
        print(f"::error::{error}; nothing was pushed", file=sys.stderr)
        return 1
    if found:
        print(f"{version} moves the aliases: {', '.join(found)}", file=sys.stderr)
    else:
        print(f"::notice::{version} moves no alias: it is published only as itself",
              file=sys.stderr)  # fmt: skip
    print(" ".join(found))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
