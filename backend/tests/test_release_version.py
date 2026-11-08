"""The release's version checks, run as the workflows write them (D-167, D-170): the tag pattern,
the comparison of the tag with the version files, and the Python form of a suffixed version
that CI expects from `/api/health`. A pre-release such as `v1.0.0-rc.1` is written `1.0.0-rc.1`
in `pyproject.toml`, `package.json` and both fields of `package-lock.json`; Python reports it as
`1.0.0rc1`, and the app shows it as a preview."""

import json
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
from packaging.version import Version

from portfolio_tracker import __version__
from portfolio_tracker.build_info import is_preview

ROOT = Path(__file__).resolve().parents[2]
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def tag_pattern() -> str:
    found = re.search(r"grep -Eq '(\^v.*?\$)'", RELEASE)
    assert found
    return found[1]


def files_check() -> str:
    """The Python script of the verify step that compares the tag with the version files."""
    found = re.search(r'python3 - "\$version" <<\'EOF\'\n(.*?)\n\s*EOF\n', RELEASE, re.S)
    assert found
    return textwrap.dedent(found[1])


def python_form(workflow: str, version: str) -> str:
    """The workflow's own sed turning `X.Y.Z-rc.N` into Python's `X.Y.ZrcN`."""
    found = re.search(r"sed -E '([^']+)'\)", workflow)
    assert found
    done = subprocess.run(
        ["sed", "-E", found[1]], input=version, capture_output=True, text=True, check=True
    )
    return done.stdout


def write_tree(folder: Path, python: str, web: str, lock: str, lock_root: str) -> None:
    (folder / "backend").mkdir()
    (folder / "frontend").mkdir()
    (folder / "backend" / "pyproject.toml").write_text(f'[project]\nversion = "{python}"\n')
    (folder / "frontend" / "package.json").write_text(json.dumps({"version": web}))
    (folder / "frontend" / "package-lock.json").write_text(
        json.dumps({"version": lock, "packages": {"": {"version": lock_root}}})
    )


def run_check(folder: Path, version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-", version],
        input=files_check(),
        cwd=folder,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("tag", "accepted"),
    [
        ("v1.0.0-rc.1", True),
        ("v1.0.0", True),
        ("v1.0.0rc1", False),
        ("v1.0.0-rc1", False),
        ("v1.0.0-RC.1", False),
    ],
)
def test_the_tag_pattern_accepts_the_rc_as_written(tag: str, accepted: bool) -> None:
    assert (re.fullmatch(tag_pattern().strip("^$"), tag) is not None) is accepted


def test_the_rc_tag_and_the_version_files_are_one_version(tmp_path: Path) -> None:
    write_tree(tmp_path, "1.0.0-rc.1", "1.0.0-rc.1", "1.0.0-rc.1", "1.0.0-rc.1")
    done = run_check(tmp_path, "1.0.0-rc.1")
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize(
    "files",
    [
        ("1.0.0rc1", "1.0.0-rc.1", "1.0.0-rc.1", "1.0.0-rc.1"),  # Python's form in pyproject
        ("1.0.0-rc.1", "1.0.0-rc.1", "1.0.0-rc.1", "0.9.0"),  # the lock's root entry forgotten
        ("1.0.0", "1.0.0-rc.1", "1.0.0-rc.1", "1.0.0-rc.1"),
    ],
)
def test_a_version_file_written_otherwise_stops_the_release(
    tmp_path: Path, files: tuple[str, str, str, str]
) -> None:
    write_tree(tmp_path, *files)
    done = run_check(tmp_path, "1.0.0-rc.1")
    assert done.returncode == 1
    assert "::error::the tag says 1.0.0-rc.1" in done.stdout


def test_both_workflows_expect_pythons_form_from_health() -> None:
    for workflow in (RELEASE, CI):
        assert python_form(workflow, "1.0.0-rc.1") == "1.0.0rc1"
        assert python_form(workflow, "1.0.0-beta.2") == "1.0.0b2"
        assert python_form(workflow, "1.0.0") == "1.0.0"
    # The form CI expects is the one Python gives the installed package (PEP 440).
    assert str(Version("1.0.0-rc.1")) == "1.0.0rc1"


def test_the_running_app_reports_this_version_as_ci_expects_it() -> None:
    written = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    assert __version__ == python_form(CI, written) == str(Version(written))
    expected = Version(written).is_prerelease or Version(written).major == 0
    assert is_preview(__version__) is expected


def test_an_rc_is_a_preview() -> None:
    assert is_preview("1.0.0rc1") and is_preview("1.0.0-rc.1")
    assert not is_preview("1.0.0")
