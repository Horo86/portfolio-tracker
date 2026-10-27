"""The timing script of the 2-second check (D-154), on the reference data at a tiny scale."""

import contextlib
import importlib.util
import io
import json
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest

from portfolio_tracker.config import Settings

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def timing() -> ModuleType:
    return load("reference_timing")


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    dev_data = load("dev_data")
    settings = Settings(data_dir=tmp_path_factory.mktemp("timing") / ".data-ref")
    scale = dev_data.Scale(users=1, transactions=60, securities=10, years=1)
    with contextlib.redirect_stdout(io.StringIO()):
        assert dev_data.main(["--development", "--reference"], settings, scale=scale) == 0
    return settings


def transaction_count(settings: Settings) -> int:
    with sqlite3.connect(settings.db_path) as db:
        return db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


def test_times_every_screen_and_leaves_the_data_as_it_was(
    timing: ModuleType, reference: Settings, tmp_path: Path
) -> None:
    before = transaction_count(reference)
    out, numbers = io.StringIO(), tmp_path / "numbers.json"
    with contextlib.redirect_stdout(out):
        assert timing.main(["--runs", "2", "--json", str(numbers)], reference) == 0
    printed = out.getvalue()
    for label in (
        "dashboard, all accounts, cold",
        "dashboard, all accounts",
        "dashboard, largest account",
        "/portfolio/summary",
        "/portfolio/positions?include_closed=true",
        "/portfolio/breakdown",
        "form preview",
        "after a change",
        "delete",
        "holding detail",
        "transactions, first page",
        "accounts",
    ):
        assert label in printed, label
    assert "within" in printed or "over" in printed
    saved = json.loads(numbers.read_text())
    assert saved["user"] == "ref01" and saved["runs"] == 2
    assert all({"median", "p90", "max"} <= set(row) for row in saved["results"])
    assert transaction_count(reference) == before  # the created transaction is deleted again


def test_refuses_an_address_that_is_not_this_computer(
    timing: ModuleType, reference: Settings
) -> None:
    for url in ("http://192.168.1.10:8000", "https://example.org", "http://127.0.0.1.example.org"):
        assert timing.main(["--url", url], reference) == 2


def test_refuses_a_folder_that_is_not_development_data(
    timing: ModuleType, reference: Settings, tmp_path: Path
) -> None:
    real = Settings(data_dir=tmp_path / "data")  # a database there, but not a .data folder
    real.db_path.parent.mkdir(parents=True)
    real.db_path.write_bytes(reference.db_path.read_bytes())
    assert timing.main([], real) == 2
    assert transaction_count(real) == transaction_count(reference)
    empty = Settings(data_dir=tmp_path / ".data-none")
    assert timing.main([], empty) == 2
    assert not empty.db_path.exists()  # never creates a database


def test_refuses_data_that_is_not_the_reference_data(timing: ModuleType, tmp_path: Path) -> None:
    demo = Settings(data_dir=tmp_path / ".data-demo")
    with contextlib.redirect_stdout(io.StringIO()):
        assert load("dev_data").main(["--development"], demo) == 0
    before = transaction_count(demo)
    assert timing.main(["--runs", "1"], demo) == 2  # no ref01 there: nothing written
    assert transaction_count(demo) == before


def test_needs_at_least_one_run(timing: ModuleType, reference: Settings) -> None:
    with pytest.raises(SystemExit):
        timing.main(["--runs", "0"], reference)
