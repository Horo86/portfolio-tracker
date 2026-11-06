"""The README embeds `docker-compose.example.yml` as it is, and the compose file's healthcheck
is the image's own (D-167): a change to one without the other fails here."""

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_readme_embeds_the_compose_file_as_it_is() -> None:
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    blocks = re.findall(r"^```yaml\n(.*?)^```$", readme, flags=re.MULTILINE | re.DOTALL)
    assert blocks == [compose]


def test_the_compose_healthcheck_is_the_images() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")

    image = re.search(r"^HEALTHCHECK .*?\n\s+CMD (\[.*\])$", dockerfile, flags=re.MULTILINE)
    service = re.search(r"^\s+test: (\[.*\])$", compose, flags=re.MULTILINE)
    assert image and service
    assert json.loads(service[1]) == ["CMD", *json.loads(image[1])]


def test_the_compose_file_names_this_version() -> None:
    """The README's quick start pulls the version being released (D-167); a suffixed version
    (`1.0.0-rc.1`) is never the one the README offers, so it is not checked."""
    version = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    compose = (ROOT / "docker-compose.example.yml").read_text(encoding="utf-8")

    image = re.search(r"^\s+image: ghcr\.io/horo86/portfolio-tracker:(\S+)$", compose, re.M)
    assert image
    if re.fullmatch(r"\d+\.\d+\.\d+", version):
        assert image[1] == version
