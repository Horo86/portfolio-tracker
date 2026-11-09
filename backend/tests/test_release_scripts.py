"""Unit tests of the release scripts in `.github/scripts/` that decide something: the upgrade
fixture gate (D-168 (7)), the image check against the previous release's image, the image
aliases and the release line (D-170 (3), (4)), and the order of release.yml's publish steps."""

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
    """The index in the repository handles every release so far: 0.9.0, a preview listed as
    unsupported (D-173), its data refused; a stable tag that it does not list still fails."""
    index = json.loads((Path(__file__).parent / "fixtures/upgrade/index.json").read_text("utf-8"))
    tags = ["v0.9.0", "v1.0.0-rc.1", "v1.0.0-rc.2", "v1.0.0"]
    assert gate.missing("1.0.0", tags, index) == []
    assert gate.missing("1.0.1", [*tags, "v1.0.1"], index) == ["1.0.0"]


def test_unsupported_counts_only_for_the_versions_it_lists() -> None:
    index = {"unsupported": [{"version": "0.9.0", "revision": "0006", "note": "preview"}]}
    assert gate.covered(index) == {"0.9.0"}
    assert gate.missing("1.0.0", ["v0.9.0", "v0.9.1", "v1.0.0"], index) == ["0.9.1"]


@pytest.mark.parametrize("version", ["1.0", "v1.0.0.1", "1.0.0-rc1", ""])
def test_a_version_that_is_not_plain_is_treated_as_a_pre_release(version: str) -> None:
    assert gate.missing(version, ["v0.9.0"], {}) is None


image_check = load("upgrade-image-check")


def test_the_image_file_lists_one_check_per_line(tmp_path: Path) -> None:
    path = tmp_path / "upgrade-from.txt"
    path.write_text(
        "# a comment\n\n  upgrade ghcr.io/example/app:1.0.0@sha256:abc  \n"
        "refuse ghcr.io/example/app:0.9.0@sha256:def\n"
    )
    assert image_check.checks_from(path) == [
        ("upgrade", "ghcr.io/example/app:1.0.0@sha256:abc"),
        ("refuse", "ghcr.io/example/app:0.9.0@sha256:def"),
    ]
    for bad in ("# only comments\n", "ghcr.io/example/app:1.0.0\n", "skip ghcr.io/x:1\n"):
        path.write_text(bad)
        with pytest.raises(image_check.CheckFailed):
            image_check.checks_from(path)


def test_the_repository_checks_the_preview_refusal_with_an_image_pinned_by_digest() -> None:
    """Until 1.0.0-rc.2 is published there is no supported image to upgrade from (D-173): the
    0.9.0 image writes a preview folder, which the new image must refuse untouched."""
    checks = image_check.checks_from(SCRIPTS.parent / "upgrade-from.txt")
    assert ("refuse", next(image for mode, image in checks if mode == "refuse")) in checks
    for _, image in checks:
        assert image.startswith("ghcr.io/horo86/portfolio-tracker:") and "@sha256:" in image
    assert any(image.split("@")[0].endswith(":0.9.0") for mode, image in checks if mode == "refuse")


def test_the_old_image_gets_the_setup_body_it_accepts() -> None:
    """A preview's setup refuses unknown fields, every later version requires them (D-173)."""
    assert set(image_check.setup_body(preview=True)) == {"username", "password", "language"}
    later = image_check.setup_body(preview=False)
    assert {"first_name", "last_name", "email"} <= set(later)
    assert later["email"].endswith("@example.org")


def test_the_folder_state_shows_any_written_or_changed_file(tmp_path: Path) -> None:
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "app.sqlite").write_bytes(b"data")
    before = image_check.folder_state(tmp_path)
    assert image_check.folder_state(tmp_path) == before
    (tmp_path / "db" / ".server.lock").write_bytes(b"")
    assert set(image_check.folder_state(tmp_path)) - set(before) == {"db/.server.lock"}


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


def git_repo(path: Path):
    """A new repository at `path` with one empty commit; returns a `git(*args)` runner there."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=path, check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    path.mkdir(exist_ok=True)
    git("init", "-q", "-b", "main")
    git("commit", "-q", "--allow-empty", "-m", "first")
    return git


aliases = load("image-aliases")


@pytest.mark.parametrize(
    ("version", "tags", "expected"),
    [
        ("1.0.0", ["v0.9.0", "v1.0.0-rc.1", "v1.0.0"], ["1.0", "1", "latest"]),
        # a fix on release/1.0 after 1.1.0: only its own minor moves
        ("1.0.1", ["v1.0.0", "v1.1.0", "v1.0.1"], ["1.0"]),
        # a fix on release/1.1 after 2.0.0: its minor and its major, never latest
        ("1.1.1", ["v1.0.0", "v1.1.0", "v2.0.0", "v1.1.1"], ["1.1", "1"]),
        ("2.0.0", ["v1.0.0", "v1.1.1", "v2.0.0"], ["2.0", "2", "latest"]),
        # versions compare as numbers, not text
        ("1.10.0", ["v1.9.0", "v1.10.0"], ["1.10", "1", "latest"]),
        ("1.9.1", ["v1.9.0", "v1.10.0", "v1.9.1"], ["1.9"]),
        # a later pre-release does not count as higher
        ("1.1.0", ["v1.1.0", "v1.2.0-rc.1", "v2.0.0-beta.1"], ["1.1", "1", "latest"]),
        # major 0: only 0.Y moves, never 0 nor latest (D-167)
        ("0.9.0", ["v0.9.0"], ["0.9"]),
        ("0.9.1", ["v0.9.0", "v0.9.1", "v1.0.0"], ["0.9"]),
    ],
)
def test_aliases_move_only_for_the_highest_plain_version_in_their_scope(
    version: str, tags: list[str], expected: list[str]
) -> None:
    assert aliases.aliases(version, tags) == expected


def test_a_re_run_of_an_older_release_moves_nothing_back() -> None:
    assert aliases.aliases("1.0.0", ["v1.0.0", "v1.0.1"]) == []
    assert aliases.aliases("1.0.1", ["v1.0.0", "v1.0.1", "v1.0.2", "v1.1.0"]) == []


@pytest.mark.parametrize("version", ["1.0.0-rc.1", "1.1.0-beta.2", "2.0.0-alpha.1"])
def test_a_pre_release_moves_no_alias(version: str) -> None:
    assert aliases.aliases(version, ["v0.9.0", f"v{version}"]) == []


@pytest.mark.parametrize("tags", [[], ["v0.9.0", "v1.0.1"], ["v1.0.0-rc.1"]])
def test_aliases_fail_closed_when_the_released_tag_was_not_read(tags: list[str]) -> None:
    with pytest.raises(aliases.Refused, match="the tags were not fetched"):
        aliases.aliases("1.0.0", tags)
    with pytest.raises(aliases.Refused, match="the tags were not fetched"):
        aliases.aliases("1.0.0-rc.2", tags)


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-rc1", "1.0.0-pre.1", ""])
def test_aliases_refuse_a_version_the_workflow_does_not_accept(version: str) -> None:
    with pytest.raises(aliases.Refused, match=r"is not X\.Y\.Z"):
        aliases.aliases(version, [f"v{version}"])


def test_main_prints_the_aliases_on_one_line_and_explains_on_stderr(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    git = git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    for tag in ("v0.9.0", "v1.0.0-rc.1", "v1.0.0"):
        git("tag", tag)
    assert aliases.main(["1.0.0"]) == 0
    out, err = capsys.readouterr()
    assert out == "1.0 1 latest\n"
    assert "1.0.0 moves the aliases: 1.0, 1, latest" in err
    assert aliases.main(["1.0.0-rc.1"]) == 0
    out, err = capsys.readouterr()
    assert out == "\n"
    assert "moves no alias" in err


def test_main_refuses_before_any_push_when_the_tag_was_not_read(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    git_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert aliases.main(["1.0.0"]) == 1
    out, err = capsys.readouterr()
    assert out == ""  # no alias printed, so the workflow has nothing to push
    assert "the tags were not fetched" in err and "nothing was pushed" in err
    assert aliases.main([]) == 1


WORKFLOW = SCRIPTS.parent / "workflows" / "release.yml"


def publish_steps() -> list[tuple[str, str]]:
    """(name, run block) of each step of release.yml's publish job, in order (text only)."""
    text = WORKFLOW.read_text("utf-8")
    job = text[text.index("\n  publish:\n") :]
    steps: list[tuple[str, str]] = []
    for chunk in job.split("\n      - ")[1:]:
        name = chunk.split("\n", 1)[0].removeprefix("name: ").removeprefix("uses: ")
        run = chunk.split("        run: |\n", 1)[1] if "        run: |\n" in chunk else ""
        steps.append(
            (name, "\n".join(line.removeprefix("          ") for line in run.splitlines()))
        )
    return steps


def test_the_aliases_are_decided_before_the_login_and_pushed_in_one_command() -> None:
    names = [name for name, _ in publish_steps()]
    decide = next(i for i, name in enumerate(names) if name.startswith("Decide the aliases"))
    login = next(i for i, name in enumerate(names) if name.startswith("Log in"))
    push = next(i for i, name in enumerate(names) if name.startswith("Push X.Y.Z"))
    assert decide < login < push
    commands = [line.strip() for _, run in publish_steps() for line in run.splitlines()]
    pushes = [line for line in commands if line.startswith("docker push")]
    assert pushes == ['docker push --all-tags "$IMAGE"']  # one push, after the local names
    assert not (SCRIPTS / "minor-tag.sh").exists()
    assert "minor-tag" not in WORKFLOW.read_text("utf-8")


def run_decide_step(tmp_path: Path, tags: list[str], version: str) -> tuple[int, str, str]:
    """Run the publish job's "Decide the aliases" step as the runner does (bash -e -o pipefail),
    in a repository with `tags`; returns the exit code, its output and what it put in the env."""
    script = next(run for name, run in publish_steps() if name.startswith("Decide the aliases"))
    repo = tmp_path / "repo"
    git = git_repo(repo)
    for tag in tags:
        git("tag", tag)
    (repo / ".github").symlink_to(SCRIPTS.parent, target_is_directory=True)
    env_file = tmp_path / "github_env"
    env_file.write_text("")
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=repo, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "VERSION": version, "GITHUB_ENV": str(env_file)},
    )  # fmt: skip
    return result.returncode, result.stdout + result.stderr, env_file.read_text()


def test_the_decide_step_prints_the_aliases_and_hands_them_to_the_push(tmp_path: Path) -> None:
    code, output, env = run_decide_step(tmp_path, ["v0.9.0", "v1.0.0"], "1.0.0")
    assert code == 0, output
    assert "this release pushes 1.0.0 and the aliases: 1.0 1 latest" in output
    assert env == "ALIASES=1.0 1 latest\n"


def test_the_decide_step_stops_the_job_when_the_tag_was_not_read(tmp_path: Path) -> None:
    code, output, env = run_decide_step(tmp_path, ["v0.9.0"], "1.0.0")
    assert code != 0
    assert "the tags were not fetched" in output
    assert env == ""  # no ALIASES for the push step, and the job stops here


release_line = load("release-line")


@pytest.fixture
def lines(tmp_path: Path, monkeypatch):
    """A remote with main and release/1.0, cloned; returns (git in the clone, commits)."""
    remote = tmp_path / "remote"
    git = git_repo(remote)
    commits = {"main": git("rev-parse", "HEAD")}
    git("switch", "-q", "-c", "release/1.0")
    git("commit", "-q", "--allow-empty", "-m", "fix on 1.0")
    commits["release/1.0"] = git("rev-parse", "HEAD")
    git("switch", "-q", "-c", "feat/other")
    git("commit", "-q", "--allow-empty", "-m", "elsewhere")
    commits["feat/other"] = git("rev-parse", "HEAD")
    git("switch", "-q", "main")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--single-branch", str(remote), str(clone)], check=True)
    # the clone knows only main, like a checkout of a tag; it still has every commit object
    subprocess.run(["git", "fetch", "-q", "origin", commits["feat/other"]], cwd=clone, check=True)
    subprocess.run(["git", "fetch", "-q", "origin", commits["release/1.0"]], cwd=clone, check=True)
    monkeypatch.chdir(clone)
    return git, commits


def test_a_commit_on_main_is_accepted(lines) -> None:
    _, commits = lines
    assert release_line.line("1.1.0", commits["main"]) == "main"
    assert release_line.line("1.1.0-rc.1", commits["main"]) == "main"


def test_a_commit_on_release_x_y_is_accepted_only_for_its_own_x_y(lines) -> None:
    _, commits = lines
    assert release_line.line("1.0.2", commits["release/1.0"]) == "release/1.0"
    assert release_line.line("1.0.2-rc.1", commits["release/1.0"]) == "release/1.0"
    with pytest.raises(release_line.Refused, match=r"neither on main nor on release/1\.1"):
        release_line.line("1.1.0", commits["release/1.0"])
    with pytest.raises(release_line.Refused, match=r"neither on main nor on release/2\.0"):
        release_line.line("2.0.0", commits["release/1.0"])


def test_a_commit_on_another_branch_or_on_none_is_refused(lines) -> None:
    _, commits = lines
    with pytest.raises(release_line.Refused, match=r"neither on main nor on release/1\.0"):
        release_line.line("1.0.2", commits["feat/other"])
    with pytest.raises(release_line.Refused, match="is not a commit"):
        release_line.line("1.0.2", "0" * 40)


def test_a_version_the_workflow_does_not_accept_is_refused(lines) -> None:
    _, commits = lines
    with pytest.raises(release_line.Refused, match=r"is not X\.Y\.Z"):
        release_line.line("1.0", commits["main"])


def test_a_remote_that_cannot_be_read_is_an_error_not_a_missing_branch(lines) -> None:
    _, commits = lines
    subprocess.run(["git", "remote", "set-url", "origin", "/nonexistent/remote"], check=True)
    with pytest.raises(release_line.Refused, match="the remote cannot be read"):
        release_line.line("1.0.2", commits["release/1.0"])


def test_release_line_main_prints_the_line_or_the_error(lines, capsys) -> None:
    _, commits = lines
    assert release_line.main(["1.0.2", commits["release/1.0"]]) == 0
    assert "the tagged commit is on release/1.0" in capsys.readouterr().out
    assert release_line.main(["1.1.0", commits["release/1.0"]]) == 1
    assert "::error::the tagged commit is neither on main" in capsys.readouterr().out
    assert release_line.main([]) == 1
