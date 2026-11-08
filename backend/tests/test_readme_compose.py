"""The README's install steps and `docker-compose.example.yml` cannot disagree (D-167, D-170):
the compose file the README downloads is the one at this version's tag, its image is this
version, the lines the README asks to check are the file's own, and the compose healthcheck is
the image's. A version bump that forgets one of them fails here."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = "https://raw.githubusercontent.com/Horo86/portfolio-tracker"


def version() -> str:
    """The version as written in the version files and the tag (`1.0.0`, `1.0.0-rc.1`)."""
    pyproject = (ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    return tomllib.loads(pyproject)["project"]["version"]


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def compose() -> str:
    return (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")


def test_the_readme_downloads_the_compose_file_of_this_version() -> None:
    urls = re.findall(rf"{re.escape(RAW)}/\S+", readme())
    assert urls == [f"{RAW}/v{version()}/docker-compose.example.yml"]
    curl = re.search(r"^\s*curl -fsSL -o docker-compose\.yml (\S+)$", readme(), re.M)
    assert curl and curl[1] == urls[0]


def test_the_compose_file_runs_this_version() -> None:
    images = re.findall(r"^\s+image: ghcr\.io/horo86/portfolio-tracker:(\S+)$", compose(), re.M)
    assert images == [version()]


def test_the_lines_the_readme_asks_to_check_are_the_compose_files_own() -> None:
    blocks = re.findall(r"^( *)```yaml\n(.*?)^\1```$", readme(), flags=re.M | re.S)
    assert len(blocks) == 1
    indent, body = blocks[0]
    lines = [line.removeprefix(indent) for line in body.splitlines()]
    own = compose().splitlines()
    assert lines and all(line in own for line in lines), [x for x in lines if x not in own]
    assert f"    image: ghcr.io/horo86/portfolio-tracker:{version()}" in lines


def test_the_compose_healthcheck_is_the_images() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^HEALTHCHECK .*?\n\s+CMD (\[.*\])$", dockerfile, flags=re.MULTILINE)
    service = re.search(r"^\s+test: (\[.*\])$", compose(), flags=re.MULTILINE)
    assert image and service
    assert json.loads(service[1]) == ["CMD", *json.loads(image[1])]
