"""CHANGELOG.md: its format, and the section of this version (D-173 (3)).

Like the README's compose test, this fails as soon as the version files name a version whose
section is missing: the version bump must move "Unreleased" under the new number. The same
rules are read by the app's build (`frontend/scripts/release-notes.mjs`) and by the release
workflow (`.github/scripts/release-notes.py`).
"""

import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def load_notes():
    path = ROOT / ".github" / "scripts" / "release-notes.py"
    spec = importlib.util.spec_from_file_location("release_notes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notes = load_notes()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text("utf-8")


def project_version() -> str:
    pyproject = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text("utf-8"))
    return pyproject["project"]["version"]


def test_the_changelog_has_its_format() -> None:
    sections = notes.parse(CHANGELOG)
    assert next(iter(sections)) == "Unreleased"
    assert {"0.9.0", "1.0.0-rc.1"} <= set(sections)


def test_the_section_of_this_version_is_there_and_says_something() -> None:
    assert notes.section(project_version(), CHANGELOG).startswith("### ")


def test_the_unreleased_section_lists_what_pull_requests_merged() -> None:
    """Between two versions "Unreleased" collects the changes; right after a version bump it
    may be empty, never missing."""
    assert "Unreleased" in notes.parse(CHANGELOG)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("Changelog\n", "first line"),
        ("# Changelog\n\n## 1.0.0\n", "first section must be"),
        ("# Changelog\n\n## Unreleased\n\n## 1.0.0-rc.1\n\n## 1.0.0\n", "newest first"),
        ("# Changelog\n\n## Unreleased\n\n## 1.0\n", "not a version"),
        ("# Changelog\n\n## Unreleased\n- an item\n", "needs a ### group"),
        ("# Changelog\n\n## Unreleased\n### Misc\n", "a group is one of"),
        ("# Changelog\n\n## Unreleased\n### Added\nplain text\n", "only ### groups"),
        ("# Changelog\n\n## Unreleased\n### Added\n  continued\n", "only ### groups"),
        ("# Changelog\n\n## Unreleased\n\n## 1.0.0\n\n## 1.0.0\n", "appears twice"),
        ("# Changelog\n- early\n## Unreleased\n", "only text before"),
    ],
)
def test_anything_outside_the_format_is_refused(text: str, error: str) -> None:
    with pytest.raises(notes.ChangelogError, match=error):
        notes.parse(text)


def test_versions_are_ordered_as_semantic_versions() -> None:
    ordered = ["1.10.0", "1.9.1", "1.0.0", "1.0.0-rc.10", "1.0.0-rc.2", "1.0.0-beta.1", "0.9.0"]
    assert sorted(reversed(ordered), key=notes.order, reverse=True) == ordered


def test_a_section_is_written_out_for_the_github_release(tmp_path: Path, monkeypatch) -> None:
    text = (
        "# Changelog\n\nIntro.\n\n## Unreleased\n\n## 1.0.0\n\n### Added\n- One `thing`\n\n"
        "### Fixed\n- Another\n\n## 0.9.0\n\n### Added\n- First\n"
    )
    assert notes.section("1.0.0", text) == "### Added\n- One `thing`\n\n### Fixed\n- Another\n"
    with pytest.raises(notes.ChangelogError, match=r"no notes for 1\.1\.0"):
        notes.section("1.1.0", text)
    with pytest.raises(notes.ChangelogError, match="no notes for Unreleased"):
        notes.section("Unreleased", text)

    (tmp_path / "CHANGELOG.md").write_text(text)
    monkeypatch.chdir(tmp_path)
    assert notes.main(["1.0.0", "--output", "notes.md"]) == 0
    assert (tmp_path / "notes.md").read_text().startswith("### Added\n- One `thing`")
    assert notes.main(["2.0.0"]) == 1
