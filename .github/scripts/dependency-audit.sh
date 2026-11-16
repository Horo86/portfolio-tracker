#!/usr/bin/env bash
# Runs one dependency audit command and decides what a finding does to the run.
#
#   AUDIT_BLOCKS=true   the audit's failure fails the step: a release (release.yml calls ci.yml
#                       with audit_blocks: true), and whenever the variable is not set.
#   AUDIT_BLOCKS=false  the audit still runs and prints everything; a failure becomes a warning
#                       on the run and a note in its summary, and the step passes: pull requests
#                       and main, where a new advisory on a dependency has nothing to do with
#                       the change under test.
#
# Usage: dependency-audit.sh "<name of the audit>" <command> [arguments...]
set -uo pipefail

name="$1"
shift

if "$@"; then
  exit 0
else
  status=$?
fi

if [ "${AUDIT_BLOCKS:-true}" != "false" ]; then
  echo "::error title=${name}::The audit failed (exit ${status}): see the log of this step. A release is not published until the package is updated or the finding is an exception in docs/development.md."
  exit "$status"
fi

echo "::warning title=${name}::The audit failed (exit ${status}): see the log of this step. It does not fail this run, but it stops the next release until the package is updated or the finding is an exception in docs/development.md."
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### ${name}: findings (report-only in this run)"
    echo
    echo "The audit failed with exit code ${status}; its output is in the step's log. This run is not failed by it. **A release is blocked** until the package is updated or the finding is an exception in \`docs/development.md\`."
  } >> "$GITHUB_STEP_SUMMARY"
fi
exit 0
