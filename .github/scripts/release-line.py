"""Check that the tagged commit is on a line a release may come from (D-170 (3)).

A tag vX.Y.Z (or a pre-release of it) is accepted when its commit is on the remote's main, or
on the remote's maintenance branch release/X.Y with the tag's own X.Y; a commit on any other
branch, or on none, is refused. The branch name is made only from the version, which release.yml
has already checked against its strict pattern. main comes with the checkout (fetch-depth: 0);
release/X.Y is looked up on the remote by name and fetched only when it exists, so a missing
branch means "not on it" while a remote that cannot be read is an error.

Usage: release-line.py VERSION COMMIT [REMOTE]   (exit 0 = go, 1 = refused; prints the line)
"""

import re
import subprocess
import sys

RELEASE = re.compile(r"(\d+)\.(\d+)\.\d+(-(alpha|beta|rc)\.\d+)?")


class Refused(Exception):
    pass


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], check=check, capture_output=True, text=True)


def on(commit: str, ref: str) -> bool:
    result = git("merge-base", "--is-ancestor", commit, ref, check=False)
    if result.returncode not in (0, 1):
        raise Refused(f"git cannot compare {commit} with {ref}: {result.stderr.strip()}")
    return result.returncode == 0


def line(version: str, commit: str, remote: str = "origin") -> str:
    """The branch the commit is on: "main" or "release/X.Y". Raises `Refused`."""
    match = RELEASE.fullmatch(version)
    if match is None:
        raise Refused(f"{version!r} is not X.Y.Z or X.Y.Z-(alpha|beta|rc).N")
    if git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False).returncode:
        raise Refused(f"{commit} is not a commit")
    if on(commit, f"refs/remotes/{remote}/main"):
        return "main"
    branch = f"release/{match[1]}.{match[2]}"
    listed = git("ls-remote", "--heads", remote, f"refs/heads/{branch}", check=False)
    if listed.returncode:
        raise Refused(f"the remote cannot be read: {listed.stderr.strip()}")
    if listed.stdout.strip():
        refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
        fetched = git("fetch", "--no-tags", remote, refspec, check=False)
        if fetched.returncode:
            raise Refused(f"{branch} cannot be fetched: {fetched.stderr.strip()}")
        if on(commit, f"refs/remotes/{remote}/{branch}"):
            return branch
    raise Refused(f"the tagged commit is neither on main nor on {branch}")


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 1
    try:
        found = line(*argv)
    except Refused as error:
        print(f"::error::{error}")
        return 1
    print(f"the tagged commit is on {found}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
