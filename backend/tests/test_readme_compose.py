"""The README embeds `docker-compose.example.yml` as it is, and the compose file's healthcheck
is the image's own (D-167): a change to one without the other fails here."""

import json
import re
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
