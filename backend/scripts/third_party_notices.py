"""Write the server's third-party notices (D-167): every Python package installed next to the
app, with its version, its licence and the text of its licence files.

Run in the image build, on the virtual environment with no development packages:

    python scripts/third_party_notices.py --output DIR

It writes `DIR/BACKEND-THIRD-PARTY.txt` (the notices) and `DIR/backend.json` (name, version
and licence of each package, for the About page). The licence is read from the package's
metadata in this order: `License-Expression`, `License` (a one-line name; a longer value is
the licence text itself), the `License ::` classifiers; with none of them, a licence file
alone still counts (the JSON then has no licence name). The script fails when a package gives
none of these, so the notices are never shipped incomplete. Standard library only, no network.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from importlib.metadata import Distribution, distributions
from pathlib import Path

TEXT_NAME = "BACKEND-THIRD-PARTY.txt"
JSON_NAME = "backend.json"
# The app itself is not a third party.
SELF = "portfolio-tracker"
# A file in the package's .dist-info folder whose name holds one of these words is a licence
# text: LICENSE, LICENSE.txt, COPYING, NOTICE, LICENSE-MIT, mit.LICENSE, licenses/apache-2.0.txt.
LICENCE_FILE = re.compile(r"licen[cs]e|copying|notice", re.IGNORECASE)
# A `License` value longer than this, or on more than one line, is a text, not a name.
NAME_MAX = 100
RULE = "-" * 78


@dataclass(frozen=True)
class Notice:
    name: str
    version: str
    licence: str | None
    texts: tuple[tuple[str, str], ...]  # (file name, text)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_name(dist: Distribution) -> str | None:
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = (meta.get("License") or "").strip()
    if declared and declared.upper() != "UNKNOWN" and is_name(declared):
        return declared
    classifiers = [
        value.split("::")[-1].strip()
        for value in meta.get_all("Classifier") or []
        if value.startswith("License ::") and value.strip() != "License :: OSI Approved"
    ]
    return "; ".join(classifiers) or None


def is_name(value: str) -> bool:
    return "\n" not in value and len(value) <= NAME_MAX


def licence_texts(dist: Distribution) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for file in dist.files or []:
        if not file.parts or not file.parts[0].endswith(".dist-info"):
            continue
        if not LICENCE_FILE.search("/".join(file.parts[1:])):
            continue
        text = Path(dist.locate_file(file)).read_text(encoding="utf-8", errors="replace").strip()
        if text:
            found.append(("/".join(file.parts[1:]), text))
    if not found:
        # Some packages put the whole licence text in the `License` field.
        declared = (dist.metadata.get("License") or "").strip()
        if declared and not is_name(declared):
            found.append(("License (from the package metadata)", declared))
    return tuple(sorted(found))


def collect(dists: list[Distribution]) -> tuple[list[Notice], list[str]]:
    """The notices, sorted by name, and the packages that give no licence at all."""
    notices: dict[str, Notice] = {}
    for dist in dists:
        name = dist.metadata.get("Name")
        if not name or canonical(name) == SELF or canonical(name) in notices:
            continue
        notices[canonical(name)] = Notice(
            name=name,
            version=dist.version,
            licence=licence_name(dist),
            texts=licence_texts(dist),
        )
    ordered = [notices[key] for key in sorted(notices)]
    missing = [f"{n.name}=={n.version}" for n in ordered if n.licence is None and not n.texts]
    return ordered, missing


def render_text(notices: list[Notice]) -> str:
    header = [
        "Portfolio Tracker: third-party software in the server",
        "",
        "The server includes the following Python packages. Each is listed with its version, its",
        "licence and the text of its licence files, as published by its authors. Portfolio",
        "Tracker itself is licensed under the GNU Affero General Public License v3",
        "(AGPL-3.0-only), in /app/LICENSE in the image. The web app's packages are listed in",
        "THIRD-PARTY.txt, next to this file.",
        "",
    ]
    body = []
    for notice in notices:
        lines = [RULE, f"{notice.name}=={notice.version}"]
        lines.append(f"Licence: {notice.licence or 'see the licence text below'}")
        for file_name, text in notice.texts:
            lines += ["", f"{file_name}:", "", text]
        if not notice.texts:
            lines += ["", "(the package ships no licence file)"]
        lines.append("")
        body.append("\n".join(lines))
    return "\n".join([*header, *body, RULE, ""])


def render_json(notices: list[Notice]) -> str:
    rows = [{"name": n.name, "version": n.version, "license": n.licence} for n in notices]
    return json.dumps(rows, indent=1, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True, help="folder to write into")
    parser.add_argument(
        "--path",
        action="append",
        help="look for packages only in this folder (repeatable; default: this Python's path)",
    )
    args = parser.parse_args(argv)
    dists = list(distributions(path=args.path) if args.path else distributions())
    notices, missing = collect(dists)
    if not notices or missing:
        print(
            "third-party notices: "
            + (f"no licence for {', '.join(missing)}" if missing else "no package found"),
            file=sys.stderr,
        )
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / TEXT_NAME).write_text(render_text(notices), encoding="utf-8")
    (args.output / JSON_NAME).write_text(render_json(notices), encoding="utf-8")
    print(f"third-party notices: {len(notices)} packages in {TEXT_NAME} and {JSON_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
