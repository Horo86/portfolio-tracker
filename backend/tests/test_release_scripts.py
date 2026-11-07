"""Unit tests of the release scripts in `.github/scripts/` that decide something: the upgrade
fixture gate (D-168 (7)) and the image check against the previous release's image."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / ".github" / "scripts"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = load("upgrade-fixture-gate")

INDEX = {
    "fixtures": [
        {"version": "0.9.0", "revision": "0006", "file": "0.9.0.sqlite",
         "expected": "0.9.0.expected.json"},
        {"version": "1.0.0", "revision": "0006", "file": "1.0.0.sqlite",
         "expected": "1.0.0.expected.json"},
        {"version": "1.0.1", "same_as": "1.0.0"},
        {"version": "1.0.2", "same_as": "9.9.9"},  # points to nothing: not covered
    ]
}  # fmt: skip


def test_a_pre_release_is_not_gated() -> None:
    assert gate.missing("1.1.0-rc.1", ["v0.9.0", "v1.0.0", "v1.0.3"], {}) is None
    assert gate.missing("2.0.0-beta.2", [], {}) is None


def test_the_first_release_and_releases_with_every_fixture_pass() -> None:
    assert gate.missing("0.9.0", ["v0.9.0"], {}) == []
    assert gate.missing("1.0.0", ["v0.9.0", "v1.0.0-rc.1", "v1.0.0"], INDEX) == []
    tags = ["v0.9.0", "v1.0.0", "v1.0.1", "v1.1.0-rc.1", "v1.1.0"]
    assert gate.missing("1.1.0", tags, INDEX) == []


@pytest.mark.parametrize("tags", [[], ["v0.9.0", "v1.0.0"], ["v1.1.0-rc.1"]])
def test_the_gate_fails_closed_when_the_released_tag_was_not_read(tags: list[str]) -> None:
    """An empty list, or one without the tag being released: the tags were not fetched, so
    "no earlier release" cannot be told apart from "none read", and the gate refuses."""
    with pytest.raises(gate.TagsNotFetched, match="the tags were not fetched"):
        gate.missing("1.1.0", tags, INDEX)


def test_same_as_counts_only_when_it_points_to_a_fixture_file() -> None:
    assert gate.covered(INDEX) == {"0.9.0", "1.0.0", "1.0.1"}
    assert gate.missing("1.1.0", ["v1.0.1", "v1.0.2", "v1.1.0"], INDEX) == ["1.0.2"]


def test_every_earlier_stable_release_is_needed_not_only_the_newest() -> None:
    tags = ["v0.9.0", "v0.9.1", "v1.0.0", "v1.0.0-rc.2", "v1.0.1"]
    assert gate.missing("1.0.1", tags, INDEX) == ["0.9.1"]


def test_later_and_equal_tags_are_ignored() -> None:
    # A fix on release/1.0 after 1.1.0 is out: only versions below it count.
    assert gate.missing("1.0.1", ["v0.9.0", "v1.0.0", "v1.1.0", "v1.0.1"], INDEX) == []
    assert gate.missing("1.0.10", ["v1.0.9", "v1.0.10"], INDEX) == ["1.0.9"]  # numeric order


def test_main_reads_the_tags_and_the_index(tmp_path: Path, monkeypatch, capsys) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q",
        "--allow-empty", "-m", "x")  # fmt: skip
    monkeypatch.chdir(tmp_path)

    assert gate.main(["1.1.0"]) == 1  # no tag at all: fails closed
    assert "the tags were not fetched" in capsys.readouterr().out
    git("tag", "v0.9.0")
    git("tag", "v1.0.0")
    git("tag", "v1.1.0")
    assert gate.main(["1.1.0"]) == 1  # no index at all
    assert "0.9.0, 1.0.0" in capsys.readouterr().out
    index = tmp_path / "backend/tests/fixtures/upgrade/index.json"
    index.parent.mkdir(parents=True)
    index.write_text(json.dumps(INDEX))
    assert gate.main(["1.1.0"]) == 0
    assert gate.main(["1.1.0-rc.1"]) == 0
    assert gate.main([]) == 1


def test_the_repository_gate_passes_for_the_next_release() -> None:
    """The fixtures in the repository cover every release so far (today 0.9.0)."""
    index = json.loads((Path(__file__).parent / "fixtures/upgrade/index.json").read_text("utf-8"))
    assert gate.missing("1.0.0", ["v0.9.0", "v1.0.0"], index) == []


@pytest.mark.parametrize("version", ["1.0", "v1.0.0.1", "1.0.0-rc1", ""])
def test_a_version_that_is_not_plain_is_treated_as_a_pre_release(version: str) -> None:
    assert gate.missing(version, ["v0.9.0"], {}) is None


image_check = load("upgrade-image-check")


def test_the_image_file_names_the_first_line_that_is_not_a_comment(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-from.txt"
    path.write_text("# a comment\n\n  ghcr.io/example/app:1.0.0@sha256:abc  \nother\n")
    assert image_check.image_from(path) == "ghcr.io/example/app:1.0.0@sha256:abc"
    path.write_text("# only comments\n")
    with pytest.raises(image_check.CheckFailed):
        image_check.image_from(path)


def test_the_repository_names_a_previous_image_pinned_by_digest() -> None:
    image = image_check.image_from(SCRIPTS.parent / "upgrade-from.txt")
    assert image.startswith("ghcr.io/horo86/portfolio-tracker:") and "@sha256:" in image


def test_contains_allows_new_fields_and_reports_changed_or_missing_ones() -> None:
    before = {"summary": {"cash": "1.00", "rows": [{"a": 1}]}, "csv": "x"}
    assert image_check.contains(before, before | {"new": True}) == []
    changed = {"summary": {"cash": "2.00", "rows": [{"a": 1}]}, "csv": "x"}
    assert image_check.contains(before, changed) == ["$.summary.cash: '2.00' != '1.00'"]
    assert image_check.contains(before, {"summary": {"rows": []}, "csv": "x"}) == [
        "$.summary.cash: missing", "$.summary.rows: length"]  # fmt: skip


def test_a_copy_is_expected_only_when_the_new_image_is_ahead() -> None:
    assert image_check.expected_copies("0006", "0006") == 0
    assert image_check.expected_copies("0006", "0007") == 1


def test_the_check_refuses_any_folder_but_a_new_upgrade_check_one(tmp_path: Path) -> None:
    with pytest.raises(image_check.CheckFailed, match="must contain pt-upgrade-check"):
        image_check.check("old", "new", 1, tmp_path / "data")
    (tmp_path / "pt-upgrade-check").mkdir()
    with pytest.raises(image_check.CheckFailed, match="exists"):
        image_check.check("old", "new", 1, tmp_path / "pt-upgrade-check")
