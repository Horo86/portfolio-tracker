"""The development data script (M5, Q-22): invented data only, and its guards."""

import importlib.util
import sqlite3
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker import textformats
from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev_data.py"


@pytest.fixture(scope="module")
def dev_data() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dev_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def user_count(settings: Settings) -> int:
    with sqlite3.connect(settings.db_path) as db:
        return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def test_seeds_invented_data_through_the_api(dev_data: ModuleType, tmp_path: Path) -> None:
    # market_data=True on purpose: the script turns it off (the network guard would fail).
    settings = Settings(data_dir=tmp_path / "data", market_data=True)
    assert dev_data.main(["--development"], settings) == 0
    assert user_count(settings) == 3

    with TestClient(create_app(settings.model_copy(update={"market_data": False})),
                    base_url="https://localhost") as client:  # fmt: skip
        login = client.post(
            "/api/v1/auth/login", json={"username": "demo", "password": "dev-demo-password"}
        )
        assert login.status_code == 200, login.text
        accounts = client.get("/api/v1/accounts").json()
        assert sorted((a["name"], a["currency"]) for a in accounts) == [
            ("Conto estero", "USD"),
            ("Conto titoli", "EUR"),
        ]
        assert sum(a["transaction_count"] for a in accounts) == len(dev_data.TRANSACTIONS)
        assets = client.get("/api/v1/assets").json()
        assert len(assets) == 5
        for asset in assets:
            assert asset["isin"].startswith("ZZ")
            assert textformats.isin_check_digit_ok(asset["isin"])
        eur = next(a for a in accounts if a["currency"] == "EUR")
        summary = client.get(f"/api/v1/accounts/{eur['id']}/summary").json()
        assert summary["open_positions"] == 4 and summary["partial"] is False

    with sqlite3.connect(settings.db_path) as db:
        rows = db.execute("SELECT username, role, must_change_password FROM users").fetchall()
    assert sorted(rows) == [
        ("admin", "ADMINISTRATOR", 0),
        ("demo", "INVESTOR", 0),
        ("newcomer", "INVESTOR", 1),
    ]


def test_refuses_a_database_that_is_not_empty(dev_data: ModuleType, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    assert dev_data.main(["--development"], settings) == 0
    before = settings.db_path.read_bytes()
    assert dev_data.main(["--development"], settings) == 2
    assert settings.db_path.read_bytes() == before


def test_refuses_an_instance_with_only_an_administrator(
    dev_data: ModuleType, tmp_path: Path
) -> None:
    settings = Settings(data_dir=tmp_path / "data", market_data=False)
    with TestClient(create_app(settings), base_url="https://localhost") as client:
        client.post(
            "/api/v1/setup/admin",
            json={"username": "owner", "password": "x" * 12, "language": "en"},
        )
    assert dev_data.main(["--development"], settings) == 2
    assert user_count(settings) == 1


def test_refuses_without_the_development_flag(dev_data: ModuleType, tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    assert dev_data.main([], settings) == 2
    assert not settings.db_path.exists()


def test_zz_isins_are_valid_and_belong_to_no_country(dev_data: ModuleType) -> None:
    for serial in (0, 1, 101, 999_999_999):
        isin = dev_data.zz_isin(serial)
        assert isin.startswith("ZZ") and textformats.isin_check_digit_ok(isin)
