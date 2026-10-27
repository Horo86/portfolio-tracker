"""The reference-size option of the development data script (D-154), at a tiny scale: the
same code path as the full 10 users x 5,000 transactions x 200 securities x 20 years."""

import contextlib
import importlib.util
import io
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker import textformats
from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dev_data.py"
ARGS = ["--development", "--reference"]


@pytest.fixture(scope="module")
def dev_data() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dev_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tiny(dev_data: ModuleType):
    return dev_data.Scale(users=2, transactions=60, securities=10, years=1)


@pytest.fixture(scope="module")
def seeded(
    dev_data: ModuleType, tiny, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Settings, str]:
    settings = Settings(data_dir=tmp_path_factory.mktemp("ref") / ".data-ref", market_data=True)
    # market_data=True on purpose: the script turns it off (checked below).
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert dev_data.main(ARGS, settings, scale=tiny) == 0
    return settings, out.getvalue()


def dump(settings: Settings) -> tuple[list[tuple], list[tuple]]:
    """The transactions and closes without their random ids, in a stable order."""
    with sqlite3.connect(settings.db_path) as db:
        transactions = db.execute(
            "SELECT u.username, a.name, t.type, t.trade_date, s.isin, t.quantity, t.unit_price,"
            " t.total_amount FROM transactions t JOIN accounts a ON a.id = t.account_id"
            " JOIN users u ON u.id = a.owner_id LEFT JOIN assets s ON s.id = t.asset_id"
            " ORDER BY 1, 2, 4, 3, 5, 6, 8"
        ).fetchall()
        closes = db.execute(
            "SELECT i.isin, p.day, p.close FROM instrument_prices p"
            " JOIN instrument_listings l ON l.id = p.listing_id"
            " JOIN instruments i ON i.id = l.instrument_id ORDER BY 1, 2"
        ).fetchall()
    return transactions, closes


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "dev-ref-password"}
    )
    assert response.status_code == 200, response.text


def test_every_user_holds_every_security_through_the_import(
    seeded: tuple[Settings, str], tiny
) -> None:
    settings, _ = seeded
    with sqlite3.connect(settings.db_path) as db:
        users = sorted(r[0] for r in db.execute("SELECT username FROM users"))
    assert users == ["admin", "ref01", "ref02"]

    app = create_app(settings.model_copy(update={"market_data": False}))
    with TestClient(app, base_url="https://localhost") as client:
        for username in ("ref01", "ref02"):
            login(client, username)
            accounts = client.get("/api/v1/accounts").json()
            assert sorted((a["name"], a["currency"]) for a in accounts) == [
                ("Conto USA", "USD"),
                ("Conto principale", "EUR"),
                ("Conto secondario", "EUR"),
            ]
            assert sum(a["transaction_count"] for a in accounts) == tiny.transactions
            for account in accounts:
                summary = client.get(f"/api/v1/accounts/{account['id']}/summary").json()
                assert summary["warnings"] == []  # no negative cash, nothing out of order
                assert summary["partial"] is False

            assets = client.get("/api/v1/assets").json()
            assert len(assets) == tiny.securities
            assert {a["asset_type"] for a in assets} == {"ETF", "STOCK", "BOND"}
            assert {a["quote_currency"] for a in assets} == {"EUR", "USD"}
            for asset in assets:
                assert asset["isin"].startswith("ZZ")
                assert textformats.isin_check_digit_ok(asset["isin"])

            # Every security open, priced by its dev-data listing with a fresh close.
            positions = client.get("/api/v1/portfolio/positions").json()
            assert positions["partial"] is False
            holdings = positions["positions"]
            assert len(holdings) == tiny.securities
            for holding in holdings:
                assert holding["open"] is True
                assert holding["price_status"] == "OK"
                assert holding["price_source"] == "PROVIDER"
                assert holding["price_exchange"] == "Borsa Esempio"
                assert holding["price_stale"] is False

            summary = client.get("/api/v1/portfolio/summary").json()
            assert (summary["partial"], summary["stale_fx"]) == (False, [])
            breakdown = client.get("/api/v1/portfolio/breakdown")
            assert breakdown.status_code == 200 and breakdown.json()["partial"] is False
            client.post("/api/v1/auth/logout", headers={"X-Requested-With": "portfolio-tracker"})


def test_closes_are_dev_data_on_weekdays_only(seeded: tuple[Settings, str], tiny) -> None:
    settings, _ = seeded
    with sqlite3.connect(settings.db_path) as db:
        listings = db.execute("SELECT provider, is_active, status FROM instrument_listings")
        assert set(listings.fetchall()) == {("dev-data", 1, "OK")}
        (count,) = db.execute("SELECT COUNT(*) FROM instruments").fetchone()
        assert count == tiny.securities
        days = [date.fromisoformat(d) for (d,) in db.execute("SELECT day FROM instrument_prices")]
        rates = [date.fromisoformat(d) for (d,) in db.execute("SELECT day FROM fx_rates")]
        (linked,) = db.execute(
            "SELECT COUNT(*) FROM assets WHERE instrument_id IS NULL OR isin NOT LIKE 'ZZ%'"
        ).fetchone()
    assert linked == 0
    assert all(d.weekday() < 5 for d in days + rates)
    today = datetime.now(UTC).date()
    assert max(days) <= today and (today - max(days)).days <= 3
    assert (max(days) - min(days)).days >= 360  # the whole window, one year here


def test_prints_the_logins_and_the_start_command_with_market_data_off(
    seeded: tuple[Settings, str],
) -> None:
    settings, out = seeded
    assert "ref01" in out and "dev-ref-password" in out
    assert (
        f"PT_DATA_DIR={settings.data_dir} PT_COOKIE_SECURE=false PT_MARKET_DATA=false "
        "uv run python -m portfolio_tracker"
    ) in out


def test_the_same_seed_gives_the_same_data_with_market_data_off(
    dev_data: ModuleType,
    tiny,
    seeded: tuple[Settings, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Settings] = []

    def recording(settings: Settings):
        created.append(settings)
        return create_app(settings)

    monkeypatch.setattr(dev_data, "create_app", recording)
    again = Settings(data_dir=tmp_path / ".data-ref", market_data=True)
    assert dev_data.main(ARGS, again, scale=tiny) == 0
    assert [s.market_data for s in created] == [False]  # asked for on, run with it off
    assert dump(again) == dump(seeded[0])  # same seed, same day: the same data


def test_the_reference_option_keeps_every_guard(
    dev_data: ModuleType, tiny, tmp_path: Path, seeded: tuple[Settings, str]
) -> None:
    assert dev_data.main(["--reference"], Settings(data_dir=tmp_path / ".data-x"), scale=tiny) == 2
    assert not (tmp_path / ".data-x").exists()
    for name in ("data", "real-data", "x.data"):
        settings = Settings(data_dir=tmp_path / name)
        assert dev_data.main(ARGS, settings, scale=tiny) == 2
        assert not settings.db_path.exists()
    settings = seeded[0]
    before = dump(settings)
    assert dev_data.main(ARGS, settings, scale=tiny) == 2  # not empty: nothing done
    assert dump(settings) == before


def test_the_full_size_ledgers_never_go_below_zero_in_replay_order(dev_data: ModuleType) -> None:
    """Every user's ledger at the reference size, replayed in the engine's same-day order
    (domain-rules §4) without a database: cash and quantities never go below zero (D-154)."""
    scale = dev_data.Scale()
    today = datetime.now(UTC).date()
    days = dev_data.weekdays(scale, today)
    secs = dev_data.securities(scale)
    rng = dev_data.random.Random(dev_data.REF_SEED)
    closes = [dev_data.walk(rng, sec.asset_type, len(days)) for sec in secs]
    rates = dev_data.usd_rates(len(days))
    for user in range(1, scale.users + 1):
        rows = dev_data.Ledger(scale, secs, days, closes, rates, user).rows()
        assert len(rows) == scale.transactions
        order = sorted(
            range(len(rows)),
            key=lambda i: (rows[i]["trade_date"], dev_data.ORDER[rows[i]["type"]], i),
        )
        cash: dict[str, Decimal] = {}
        held: dict[tuple[str, str], Decimal] = {}
        for i in order:
            row = rows[i]
            account, kind = row["account"], row["type"]
            total = Decimal(row["total_amount"])
            sign = -1 if kind in ("BUY", "WITHDRAWAL") else 1
            cash[account] = cash.get(account, Decimal(0)) + sign * total
            assert cash[account] >= 0, (user, row)
            if kind in ("BUY", "SELL"):
                key = (account, row["isin"])
                quantity = Decimal(row["quantity"]) * (1 if kind == "BUY" else -1)
                held[key] = held.get(key, Decimal(0)) + quantity
                assert held[key] > 0, (user, row)
        assert len(held) == scale.securities + scale.shared
