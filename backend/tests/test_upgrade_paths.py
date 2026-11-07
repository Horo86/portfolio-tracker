"""The standing upgrade gate (D-168 (7)): a database with invented data from every released
version migrates to the head at start and gives the same figures.

The fixtures are in `tests/fixtures/upgrade/` (`index.json` lists them), made by
`scripts/make_upgrade_fixture.py` from invented data only (`ZZ` ISINs). A version that changed
neither the schema nor the shape of the data may point to an earlier fixture with `same_as`.
The fake key, the fixed clock and the snapshot below are also read by the generator, so the
fixture and its check cannot drift apart.
"""

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app
from portfolio_tracker.migrations_runner import alembic_config
from portfolio_tracker.repositories import credentials

FIXTURES = Path(__file__).parent / "fixtures" / "upgrade"

# A FAKE TEST KEY, NOT A SECRET: base64 of the 32 ASCII bytes "fake-test-key-not-a-secret-00000".
# It encrypts the invented provider credential of the fixtures, and nothing else, anywhere.
FAKE_FIXTURE_KEY = "ZmFrZS10ZXN0LWtleS1ub3QtYS1zZWNyZXQtMDAwMDA="
FAKE_CREDENTIAL = ("fixture-engine", "fake-credential-value-0001")  # provider, invented value
INVESTOR = ("investor", "fixture-investor-password")  # public, like every fixture password
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # the day the figures are computed for
CSRF = {"X-Requested-With": "portfolio-tracker"}


def fixture_settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir, market_data=False, secret_key=FAKE_FIXTURE_KEY)


def fixture_app(settings: Settings) -> TestClient:
    # http + Secure cookies would be dropped by the client: same as the other tests, use https.
    return TestClient(create_app(settings, clock=lambda: FIXED_NOW), base_url="https://localhost")


def _get(client: TestClient, path: str) -> Any:
    response = client.get(f"/api/v1{path}")
    assert response.status_code == 200, (path, response.text)
    return response.text if path.endswith(".csv") else response.json()


def snapshot(client: TestClient) -> dict[str, Any]:
    """The investor's figures at FIXED_NOW: what an upgrade must not change."""
    response = client.post(
        "/api/v1/auth/login",
        headers=CSRF,
        json={"username": INVESTOR[0], "password": INVESTOR[1]},
    )
    assert response.status_code == 200, response.text
    accounts = _get(client, "/accounts")
    shot = {
        "accounts": accounts,
        "portfolio_summary": _get(client, "/portfolio/summary"),
        "portfolio_positions": _get(client, "/portfolio/positions"),
        "portfolio_breakdown": _get(client, "/portfolio/breakdown"),
        "account_summaries": {
            a["name"]: _get(client, f"/accounts/{a['id']}/summary") for a in accounts
        },
        "imports": _get(client, "/imports"),
        "export_csv": _get(client, "/transactions/export.csv"),
    }
    client.post("/api/v1/auth/logout", headers=CSRF)
    return shot


def assert_contains(expected: Any, actual: Any, where: str = "$") -> None:
    """Every value of `expected` is in `actual`: a later version may add fields, never change
    or drop one (dropping one is a deliberate change to the expected file, with its reason)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{where}: not an object"
        for key, value in expected.items():
            assert key in actual, f"{where}.{key}: missing"
            assert_contains(value, actual[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), f"{where}: length"
        for i, (want, got) in enumerate(zip(expected, actual, strict=True)):
            assert_contains(want, got, f"{where}[{i}]")
    else:
        assert actual == expected, f"{where}: {actual!r} != {expected!r}"


def index() -> list[dict[str, Any]]:
    path = FIXTURES / "index.json"  # missing only while the generator makes the first one
    return json.loads(path.read_text("utf-8"))["fixtures"] if path.exists() else []


def with_files() -> Iterator[dict[str, Any]]:
    return (entry for entry in index() if "file" in entry)


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config(Path("unused.sqlite")))
    return "-".join(sorted(script.get_heads()))


def revision_of(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def test_the_index_and_the_fixture_files_agree() -> None:
    entries = index()
    assert any("file" in entry for entry in entries), "no upgrade fixture is listed"
    versions = [entry["version"] for entry in entries]
    assert len(versions) == len(set(versions)), "a version is listed twice"
    by_version = {entry["version"]: entry for entry in entries}
    listed = set()
    for entry in entries:
        if "same_as" in entry:
            assert set(entry) == {"version", "same_as"}, entry
            target = by_version.get(entry["same_as"])
            assert target is not None and "file" in target, f"{entry}: same_as needs a fixture"
            continue
        assert {"version", "revision", "file", "expected"} <= set(entry), entry
        assert entry["file"] == f"{entry['version']}.sqlite", entry
        assert entry["expected"] == f"{entry['version']}.expected.json", entry
        assert revision_of(FIXTURES / entry["file"]) == entry["revision"], entry
        listed |= {entry["file"], entry["expected"]}
    on_disk = {path.name for path in FIXTURES.iterdir()} - {"index.json"}
    assert on_disk == listed, "a fixture file is not listed, or a listed one is missing"


def test_the_fixtures_hold_only_invented_isins() -> None:
    for entry in with_files():
        with closing(sqlite3.connect(FIXTURES / entry["file"])) as connection:
            isins = {row[0] for row in connection.execute("SELECT isin FROM assets")}
            isins |= {row[0] for row in connection.execute("SELECT isin FROM instruments")}
        assert isins - {None} and all(isin.startswith("ZZ") for isin in isins - {None}), isins


@pytest.mark.parametrize("entry", list(with_files()), ids=lambda entry: entry["version"])
def test_a_released_database_upgrades_to_the_head_with_the_same_figures(
    entry: dict[str, Any], tmp_path: Path
) -> None:
    settings = fixture_settings(tmp_path / "data")
    settings.db_path.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / entry["file"], settings.db_path)
    expected = json.loads((FIXTURES / entry["expected"]).read_text("utf-8"))
    behind = entry["revision"] != head_revision()

    with fixture_app(settings) as client:
        actual = snapshot(client)
        engine = client.app.state.engine
        with Session(engine) as session:
            assert (
                credentials.get_credential(
                    session, client.app.state.credential_cipher, FAKE_CREDENTIAL[0]
                )
                == FAKE_CREDENTIAL[1]
            )

    assert revision_of(settings.db_path) == head_revision()
    copies = sorted(settings.pre_migration_backup_dir.glob("app-*.sqlite"))
    if behind:  # one verified, recorded copy of the released database
        [copy] = copies
        assert revision_of(copy) == entry["revision"]
        assert json.loads(copy.with_suffix(".json").read_text())["status"] == "done"
    else:
        assert copies == []
    with closing(sqlite3.connect(settings.db_path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert_contains(expected, actual)


def load_generator():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "make_upgrade_fixture.py"
    spec = importlib.util.spec_from_file_location("make_upgrade_fixture", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_makes_a_fixture_and_refuses_what_it_must(tmp_path: Path) -> None:
    generator = load_generator()
    output = tmp_path / "upgrade"

    target = generator.make("9.9.9", tmp_path / ".data-work", output)

    assert target == output / "9.9.9.sqlite" and not (tmp_path / ".data-work").exists()
    assert revision_of(target) == head_revision()
    assert not target.with_name(target.name + "-wal").exists()
    [entry] = json.loads((output / "index.json").read_text())["fixtures"]
    assert entry == {"version": "9.9.9", "revision": head_revision(), "file": "9.9.9.sqlite",
                     "expected": "9.9.9.expected.json"}  # fmt: skip
    expected = json.loads((output / "9.9.9.expected.json").read_text())
    assert expected["portfolio_summary"]["day"] == FIXED_NOW.date().isoformat()

    with pytest.raises(generator.Refused, match="never replaced"):
        generator.make("9.9.9", tmp_path / ".data-other", output)
    with pytest.raises(generator.Refused, match=r"must start with \.data"):
        generator.make("9.9.8", tmp_path / "work", output)
    (tmp_path / ".data-exists").mkdir()
    with pytest.raises(generator.Refused, match="new work folder"):
        generator.make("9.9.8", tmp_path / ".data-exists", output)
    assert generator.main(["--version", "9.9.8", "--work", str(tmp_path / ".data-x")]) == 2
