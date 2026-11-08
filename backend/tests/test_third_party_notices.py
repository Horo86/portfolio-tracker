"""The server's third-party notices (`scripts/third_party_notices.py`, D-167): the licence
read in the agreed order, the licence texts, the sorted and complete list, and a failure when
a package gives no licence at all. Fake packages are written into a temporary folder; one test
runs on this development environment."""

from __future__ import annotations

import importlib.util
import json
import sys
from importlib import metadata
from pathlib import Path

import pytest
from packaging.requirements import Requirement

SCRIPT = Path(__file__).parents[1] / "scripts" / "third_party_notices.py"
spec = importlib.util.spec_from_file_location("third_party_notices", SCRIPT)
assert spec is not None and spec.loader is not None
notices = importlib.util.module_from_spec(spec)
# The script's dataclass looks its module up by name.
sys.modules[spec.name] = notices
spec.loader.exec_module(notices)


def fake_package(
    site: Path,
    name: str,
    version: str = "1.0",
    headers: tuple[str, ...] = (),
    files: dict[str, str] | None = None,
) -> None:
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    metadata = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}", *headers]
    (info / "METADATA").write_text("\n".join(metadata) + "\n", encoding="utf-8")
    record = [f"{info.name}/METADATA,,"]
    for relative, text in (files or {}).items():
        path = info / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        record.append(f"{info.name}/{relative},,")
    record.append(f"{info.name}/RECORD,,")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


def run(site: Path, out: Path) -> int:
    return notices.main(["--output", str(out), "--path", str(site)])


def rows(out: Path) -> dict[str, str | None]:
    data = json.loads((out / "backend.json").read_text(encoding="utf-8"))
    return {row["name"]: row["license"] for row in data}


def test_the_licence_is_read_in_the_agreed_order(tmp_path: Path) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    mit = "Classifier: License :: OSI Approved :: MIT License"
    fake_package(
        site, "a-expression", headers=("License-Expression: Apache-2.0", "License: BSD", mit)
    )
    fake_package(site, "b-field", headers=("License: BSD-3-Clause", mit))
    fake_package(site, "c-classifier", headers=("License: UNKNOWN", mit))
    fake_package(
        site,
        "d-two-classifiers",
        headers=(
            "Classifier: License :: OSI Approved",
            "Classifier: License :: OSI Approved :: MIT License",
            "Classifier: License :: OSI Approved :: Apache Software License",
        ),
    )
    fake_package(site, "e-file-only", files={"licenses/LICENSE.txt": "Some licence text"})
    assert run(site, out) == 0
    assert rows(out) == {
        "a-expression": "Apache-2.0",
        "b-field": "BSD-3-Clause",
        "c-classifier": "MIT License",
        "d-two-classifiers": "MIT License; Apache Software License",
        "e-file-only": None,
    }
    text = (out / "BACKEND-THIRD-PARTY.txt").read_text(encoding="utf-8")
    assert "e-file-only==1.0\nLicence: see the licence text below" in text
    assert "licenses/LICENSE.txt:\n\nSome licence text" in text


def test_a_long_license_field_is_the_text_not_the_name(tmp_path: Path) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    long_text = "Copyright (c) the authors.\n" + "Permission is hereby granted. " * 10
    fake_package(
        site,
        "long-field",
        headers=(
            "License: " + long_text.replace("\n", "\n        "),
            "Classifier: License :: OSI Approved :: MIT License",
        ),
    )
    assert run(site, out) == 0
    assert rows(out) == {"long-field": "MIT License"}
    text = (out / "BACKEND-THIRD-PARTY.txt").read_text(encoding="utf-8")
    assert "License (from the package metadata):" in text
    assert "Permission is hereby granted." in text


def test_licence_and_notice_files_are_all_included_sorted(tmp_path: Path) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    fake_package(
        site,
        "two-files",
        headers=("License-Expression: Apache-2.0",),
        files={
            "licenses/NOTICE": "The notice",
            "licenses/LICENSE": "The licence",
            "top_level.txt": "x",
        },
    )
    assert run(site, out) == 0
    text = (out / "BACKEND-THIRD-PARTY.txt").read_text(encoding="utf-8")
    assert text.index("licenses/LICENSE:") < text.index("licenses/NOTICE:")
    assert "top_level.txt" not in text


def test_a_licence_file_named_otherwise_is_found(tmp_path: Path) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    fake_package(
        site,
        "odd-names",
        headers=("License: MIT",),
        files={"mit.LICENSE": "MIT text", "COPYING": "Copying text", "README.rst": "readme"},
    )
    assert run(site, out) == 0
    text = (out / "BACKEND-THIRD-PARTY.txt").read_text(encoding="utf-8")
    assert "mit.LICENSE:\n\nMIT text" in text
    assert "COPYING:\n\nCopying text" in text
    assert "readme" not in text


def test_the_list_is_sorted_and_leaves_out_the_app_itself(tmp_path: Path) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    for name in ("Zeta", "alpha", "portfolio-tracker", "Mid_pkg"):
        fake_package(site, name, headers=("License-Expression: MIT",))
    assert run(site, out) == 0
    assert list(rows(out)) == ["alpha", "Mid_pkg", "Zeta"]


def test_a_package_with_no_licence_at_all_fails_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site, out = tmp_path / "site", tmp_path / "out"
    fake_package(site, "fine", headers=("License-Expression: MIT",))
    fake_package(site, "nothing", version="2.0", headers=("License: UNKNOWN",))
    assert run(site, out) == 1
    assert "no licence for nothing==2.0" in capsys.readouterr().err
    assert not out.exists()


def test_no_package_at_all_fails(tmp_path: Path) -> None:
    (tmp_path / "site").mkdir()
    assert run(tmp_path / "site", tmp_path / "out") == 1


def runtime_packages() -> set[str]:
    """The app's runtime dependencies, followed down (markers evaluated for this Python, no
    extras, no development group): the packages the image installs."""
    found: set[str] = set()
    todo = ["portfolio-tracker"]
    while todo:
        for line in metadata.requires(todo.pop()) or []:
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            name = notices.canonical(requirement.name)
            if name not in found:
                found.add(name)
                todo.append(name)
    return found


def test_this_environment_lists_the_server_packages_with_their_texts(tmp_path: Path) -> None:
    assert notices.main(["--output", str(tmp_path)]) == 0
    listed = rows(tmp_path)
    assert listed["fastapi"] == "MIT"
    assert listed["SQLAlchemy"] == "MIT"
    assert "portfolio-tracker" not in listed
    names = list(listed)
    assert names == sorted(names, key=notices.canonical)
    assert all(licence for licence in listed.values())
    text = (tmp_path / "BACKEND-THIRD-PARTY.txt").read_text(encoding="utf-8")
    fastapi = text[text.index("\nfastapi==") : text.index(notices.RULE, text.index("\nfastapi=="))]
    assert "Permission is hereby granted, free of charge" in fastapi
    # Every package the image installs has a licence text; a development tool may have none.
    runtime = runtime_packages()
    assert {"fastapi", "sqlalchemy", "starlette"} <= runtime
    without_text = {
        notices.canonical(block.split("==", 1)[0])
        for block in text.split(notices.RULE)
        if "(the package ships no licence file)" in block
    }
    assert not without_text & runtime, without_text & runtime
