"""The dependency audits of CI: a finding is a warning on pull requests and on main, and
fails a release (`.github/scripts/dependency-audit.sh`, `ci.yml`, `release.yml`)."""

import subprocess
from pathlib import Path

import pytest

GITHUB = Path(__file__).parents[2] / ".github"
SCRIPT = GITHUB / "scripts" / "dependency-audit.sh"
CI = (GITHUB / "workflows" / "ci.yml").read_text("utf-8")
RELEASE = (GITHUB / "workflows" / "release.yml").read_text("utf-8")


def audit(tmp_path: Path, command: list[str], blocks: str | None) -> tuple[int, str, str]:
    """Run the script as a step does; returns its exit code, its output and the summary."""
    summary = tmp_path / "summary.md"
    summary.write_text("")
    env = {"PATH": "/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary)}
    if blocks is not None:
        env["AUDIT_BLOCKS"] = blocks
    result = subprocess.run(
        # The step's shell (bash -e -o pipefail) runs the script as a command of its own.
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", '"$0" "$@"',
         str(SCRIPT), "Audit (test)", *command],
        capture_output=True, text=True, env=env,
    )  # fmt: skip
    return result.returncode, result.stdout + result.stderr, summary.read_text()


FINDING = ["bash", "-c", "echo 'invented-package 1.0 has a known vulnerability'; exit 3"]
CLEAN = ["bash", "-c", "echo 'No known vulnerabilities found'"]


@pytest.mark.parametrize("blocks", ["true", "false", None])
def test_a_clean_audit_passes_everywhere(tmp_path: Path, blocks: str | None) -> None:
    code, out, summary = audit(tmp_path, CLEAN, blocks)

    assert code == 0 and "No known vulnerabilities found" in out
    assert "::warning" not in out and "::error" not in out and summary == ""


def test_on_pull_requests_and_main_a_finding_is_shown_and_does_not_fail(tmp_path: Path) -> None:
    code, out, summary = audit(tmp_path, FINDING, "false")

    assert code == 0
    assert "invented-package 1.0 has a known vulnerability" in out  # the audit's own output
    assert "::warning title=Audit (test)::The audit failed (exit 3)" in out
    assert "stops the next release" in out
    assert "Audit (test): findings" in summary and "A release is blocked" in summary


@pytest.mark.parametrize("blocks", ["true", None, "", "yes"])  # anything but "false" blocks
def test_in_a_release_a_finding_fails_with_the_audit_s_own_code(
    tmp_path: Path, blocks: str | None
) -> None:
    code, out, summary = audit(tmp_path, FINDING, blocks)

    assert code == 3
    assert "invented-package 1.0 has a known vulnerability" in out
    assert "::error title=Audit (test)::" in out and "::warning" not in out
    assert summary == ""


def test_an_audit_that_cannot_run_is_treated_like_a_finding(tmp_path: Path) -> None:
    missing = ["/nonexistent/audit-tool"]
    assert audit(tmp_path, missing, "true")[0] == 127
    code, out, _ = audit(tmp_path, missing, "false")
    assert code == 0 and "::warning" in out


def steps(job: str) -> dict[str, str]:
    """name -> text of each step of one job of ci.yml (text only, like the release tests)."""
    body = CI[CI.index(f"\n  {job}:\n") :]
    body = body[: body.index("\n\n  ", 1)] if "\n\n  " in body[1:] else body
    chunks = body.split("\n      - ")[1:]
    return {chunk.split("\n", 1)[0].removeprefix("name: "): chunk for chunk in chunks}


def test_only_a_release_makes_the_audits_block() -> None:
    """ci.yml reads the choice from its `workflow_call` input, which pull requests and pushes
    to main do not have; release.yml is the only caller and asks for the blocking audit."""
    assert "  AUDIT_BLOCKS: ${{ inputs.audit_blocks == true }}\n" in CI
    call = CI[CI.index("  workflow_call:\n") : CI.index("\npermissions:")]
    assert "      audit_blocks:\n        type: boolean\n        default: true\n" in call
    release_ci = RELEASE[RELEASE.index("\n  ci:\n") : RELEASE.index("\n  publish:\n")]
    assert "    uses: ./.github/workflows/ci.yml\n    with:\n" in release_ci
    assert "      audit_blocks: true\n" in release_ci
    assert "AUDIT_BLOCKS" not in RELEASE  # nothing in the release overrides it


def test_both_blocking_audits_still_run_through_the_script() -> None:
    python = next(text for name, text in steps("backend").items() if "pip-audit" in name)
    web = next(text for name, text in steps("frontend").items() if "shipped packages" in name)

    assert '../.github/scripts/dependency-audit.sh "Dependency audit (Python)"' in python
    assert "uv run pip-audit --strict --disable-pip --require-hashes -r" in python
    assert '../.github/scripts/dependency-audit.sh "Dependency audit (web app)"' in web
    assert "npm audit --omit=dev --audit-level=high" in web
    for step in (python, web):  # never silenced in the workflow itself
        assert "continue-on-error" not in step and "|| true" not in step
    assert SCRIPT.stat().st_mode & 0o111  # executable: the steps call it directly
