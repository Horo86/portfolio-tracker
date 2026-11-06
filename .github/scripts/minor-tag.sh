#!/usr/bin/env bash
# Prints X.Y when the tag vVERSION is the highest plain vX.Y.Z tag of the repository, and
# nothing otherwise (a suffixed version, or a higher patch exists), so X.Y never moves back
# (D-167). release.yml runs it in the publish job just before the push, on a checkout with all
# tags, so a re-run of an older release sees the newer tags too. Reads VERSION.
set -euo pipefail

case "$VERSION" in
  *-*) exit 0 ;; # a suffixed version is published only as itself
esac
minor="${VERSION%.*}"
git tag -l "v$minor.*" > tags.txt
highest=$(grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' tags.txt | sort -V | tail -n 1 || true)
if [ "$highest" = "v$VERSION" ]; then
  printf '%s\n' "$minor"
else
  echo "::notice::${highest:-no plain v$minor.* tag} is not v$VERSION: the $minor tag stays where it is" >&2
fi
