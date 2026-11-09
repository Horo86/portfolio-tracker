"""Make the upgrade fixture of a released version (D-168 (7)): invented data only.

Run it on the release tag's own commit, right after the release, in a new development work
folder whose name starts with `.data`:

    uv run python scripts/make_upgrade_fixture.py --development --version 1.0.0 --work .data-fixture

It writes `tests/fixtures/upgrade/<version>.sqlite` (one file, compacted), the figures the test
compares (`<version>.expected.json`) and the version's entry in `index.json`, then deletes the
work folder. Everything goes through the API, except the market data, the FX rates and the
provider credential, which no route stores (written through the repository layer, as a
provider run or the administrator would). The ISINs are `ZZ` (no country: no real security);
the credential is encrypted with the fake test key of `tests/test_upgrade_paths.py`. It reads
nothing outside the repository.

A version that changed neither the schema nor the shape of the data needs no file of its own:
add `{"version": "X.Y.Z", "same_as": "<earlier version>"}` to `index.json` by hand. The first
fixture after the baseline revision (D-173) is 1.0.0-rc.2's: `--version 1.0.0-rc.2`.
"""

import argparse
import base64
import importlib.util
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata.base import PriceClass, Rate
from portfolio_tracker.repositories import credentials, fx, instruments

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "tests" / "fixtures" / "upgrade"
TEST_MODULE = BACKEND / "tests" / "test_upgrade_paths.py"
ADMIN = ("admin", "fixture-admin-password")  # public, invented
PROVIDER = "fixture-data"  # no engine has this name: nothing is ever fetched for it


def load_test_module():
    """The fake key, the fixed clock and the snapshot live in the test (one source)."""
    spec = importlib.util.spec_from_file_location("test_upgrade_paths", TEST_MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def zz_isin(serial: int) -> str:
    """A valid ISIN that belongs to no security: "ZZ" is not a country (ISO 6166 check)."""
    body = f"ZZ{serial:09d}"
    digits = "".join(str(int(ch, 36)) for ch in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n // 10 + n % 10
    return body + str((10 - total % 10) % 10)


ASSETS = {
    "world": {"name": "Fixture World Equity ETF", "asset_type": "ETF", "ticker": "ZZFWE",
              "quote_currency": "EUR", "quote_unit": "CURRENCY", "isin": zz_isin(901)},
    "stock": {"name": "Fixture Industries SpA", "asset_type": "STOCK", "ticker": "ZZFIN",
              "quote_currency": "EUR", "quote_unit": "CURRENCY", "isin": zz_isin(902)},
    "bond": {"name": "Fixture Government Bond 2031", "asset_type": "BOND",
             "quote_currency": "EUR", "quote_unit": "PERCENT_OF_NOMINAL", "isin": zz_isin(903)},
    "usd": {"name": "Fixture Robotics Inc.", "asset_type": "STOCK", "ticker": "ZZFRB",
            "quote_currency": "USD", "quote_unit": "CURRENCY", "isin": zz_isin(904)},
    "manual": {"name": "Fixture Fund priced by hand", "asset_type": "ETF",
               "quote_currency": "EUR", "quote_unit": "CURRENCY"},
}  # fmt: skip

# (account, type, day, asset, fields): every amount is invented; all seven types.
TRANSACTIONS = [
    ("eur", "DEPOSIT", "2025-01-02", None, {"total_amount": "20000.00"}),
    ("eur", "BUY", "2025-01-10", "world", {"quantity": "50", "unit_price": "100.00",
        "gross_amount": "5000.00", "fees": "2.50", "total_amount": "5002.50"}),
    ("eur", "BUY", "2025-02-03", "stock", {"quantity": "100", "unit_price": "12.00",
        "gross_amount": "1200.00", "fees": "2.00", "total_amount": "1202.00"}),
    ("eur", "BUY", "2025-02-17", "bond", {"quantity": "5000", "unit_price": "98.00",
        "gross_amount": "4900.00", "fees": "3.00", "accrued_interest_paid": "20.00",
        "taxes": "-2.50", "total_amount": "4920.50"}),
    ("eur", "BUY", "2025-03-01", "manual", {"quantity": "10", "unit_price": "80.00",
        "gross_amount": "800.00", "fees": "1.00", "total_amount": "801.00"}),
    ("eur", "DIVIDEND", "2025-05-20", "stock", {"gross_amount": "30.00", "taxes": "7.80",
        "total_amount": "22.20"}),
    ("eur", "STOCK_SPLIT", "2025-06-02", "stock", {"split_ratio": "2:1"}),
    ("eur", "SELL", "2025-06-12", "stock", {"quantity": "80", "unit_price": "7.00",
        "gross_amount": "560.00", "fees": "2.00", "taxes": "20.07", "total_amount": "537.93"}),
    ("eur", "COUPON", "2025-08-01", "bond", {"gross_amount": "75.00", "taxes": "9.38",
        "total_amount": "65.62"}),
    ("eur", "WITHDRAWAL", "2025-09-15", None, {"total_amount": "1000.00"}),
    ("usd", "DEPOSIT", "2025-03-03", None, {"total_amount": "10000.00"}),
    ("usd", "BUY", "2025-03-05", "usd", {"quantity": "20", "unit_price": "150.00",
        "gross_amount": "3000.00", "fees": "1.00", "total_amount": "3001.00"}),
]  # fmt: skip

MANUAL_PRICES = {"stock": "6.60", "bond": "99.15", "usd": "162.50", "manual": "84.20"}
MANUAL_DAY = "2025-12-30"
WORLD_CLOSES = {date(2025, 12, 29): "107.90", date(2025, 12, 30): "108.10",
                date(2025, 12, 31): "108.40"}  # fmt: skip
USD_RATES = {date(2025, 12, 30): "1.2400", date(2025, 12, 31): "1.2500"}  # USD per EUR
IMPORT_CSV = (
    "account,account_currency,trade_date,type,total_amount\n"
    "Fixture imported,EUR,2025-04-01,DEPOSIT,1500.00\n"
    "Fixture imported,EUR,2025-10-01,WITHDRAWAL,200.00\n"
)
CSRF = {"X-Requested-With": "portfolio-tracker"}


class Refused(Exception):
    pass


def _call(client: TestClient, method: str, path: str, body: dict | None = None) -> dict:
    response = client.request(method, f"/api/v1{path}", json=body, headers=CSRF)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
    return response.json() if response.content else {}


def seed(client: TestClient, investor: tuple[str, str], credential: tuple[str, str]) -> None:
    _call(client, "POST", "/setup/admin",
          {"username": ADMIN[0], "password": ADMIN[1], "language": "en"})  # fmt: skip
    new_user = {"username": investor[0], "display_name": "Fixture", "language": "it"}
    created = _call(client, "POST", "/admin/users", new_user)
    temporary = created["temporary_password"]
    _call(client, "POST", "/auth/logout")
    _call(client, "POST", "/auth/login",
          {"username": investor[0], "password": temporary})  # fmt: skip
    _call(client, "POST", "/me/password",
          {"current_password": temporary, "new_password": investor[1]})  # fmt: skip
    accounts = {
        "eur": _call(client, "POST", "/accounts",
                     {"name": "Fixture broker", "currency": "EUR", "provider": "Example Broker"}),
        "usd": _call(client, "POST", "/accounts",
                     {"name": "Fixture dollars", "currency": "USD", "provider": "Example Broker"}),
    }  # fmt: skip
    assets = {key: _call(client, "POST", "/assets", body) for key, body in ASSETS.items()}
    for account, type_, day, asset, fields in TRANSACTIONS:
        body = {"account_id": accounts[account]["id"], "type": type_, "trade_date": day}
        if asset:
            body["asset_id"] = assets[asset]["id"]
        _call(client, "POST", "/transactions", body | fields)
    for key, close in MANUAL_PRICES.items():
        _call(client, "PUT", f"/assets/{assets[key]['id']}/prices",
              {"day": MANUAL_DAY, "close": close})  # fmt: skip
    upload = {"content_base64": base64.b64encode(IMPORT_CSV.encode()).decode(),
              "file_name": "fixture.csv"}  # fmt: skip
    preview = _call(client, "POST", "/imports/preview", upload)
    selected = [line["key"] for line in preview["lines"] if line["selected"]]
    _call(client, "POST", "/imports/confirm",
          upload | {"fingerprint": preview["fingerprint"], "selected": selected})  # fmt: skip
    _call(client, "POST", "/auth/logout")

    # NOT PUBLIC PATHS: no route stores provider closes, rates or credentials (V1).
    engine, cipher = client.app.state.engine, client.app.state.credential_cipher
    world = ASSETS["world"]
    with db_transaction(engine, write=True) as session:
        instrument = instruments.get_or_create_instrument(
            session, world["isin"], world["quote_currency"]
        )
        instruments.set_price_class(session, instrument, PriceClass.ETF_STOCK, PROVIDER)
        listing = instruments.save_listing(
            session, instrument.id, PROVIDER, world["ticker"], exchange="Fixture Exchange",
            currency="EUR", unit="CURRENCY", matched_by="ISIN", status="OK",
            last_success_at=datetime(2025, 12, 31, 22, 0, tzinfo=UTC),
        )  # fmt: skip
        instruments.activate(session, listing, datetime(2025, 12, 31, 22, 0, tzinfo=UTC))
        instruments.store_closes(
            session,
            listing.id,
            (instruments.StoredClose(d, Decimal(c), None) for d, c in WORLD_CLOSES.items()),
        )
        instruments.link_asset(session, UUID(assets["world"]["id"]), instrument.id)
        fx.store_rates(
            session, [Rate(d, "USD", Decimal(r)) for d, r in USD_RATES.items()], PROVIDER
        )
        credentials.set_credential(session, cipher, credential[0], credential[1])


def compact_copy(source: Path, target: Path) -> None:
    """One file, no free pages, rollback journal (no -wal beside it)."""
    target.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("VACUUM INTO ?", (str(target),))
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")


def schema_revision(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def make(version: str, work: Path, output: Path = FIXTURES, note: str | None = None) -> Path:
    if not work.name.startswith(".data"):
        raise Refused(f"{work} is not a development work folder (its name must start with .data)")
    if work.exists():
        raise Refused(f"{work} exists: give a new work folder")
    target = output / f"{version}.sqlite"
    if target.exists():
        raise Refused(f"{target} exists: a released version's fixture is never replaced")
    tests = load_test_module()
    settings = tests.fixture_settings(work)
    try:
        with tests.fixture_app(settings) as client:
            seed(client, tests.INVESTOR, tests.FAKE_CREDENTIAL)
            expected = tests.snapshot(client)
        output.mkdir(parents=True, exist_ok=True)
        compact_copy(settings.db_path, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    (output / f"{version}.expected.json").write_text(
        json.dumps(expected, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8"
    )
    index_path = output / "index.json"
    data = json.loads(index_path.read_text("utf-8")) if index_path.exists() else {"fixtures": []}
    entry = {
        "version": version,
        "revision": schema_revision(target),
        "file": target.name,
        "expected": f"{version}.expected.json",
    }
    if note:
        entry["note"] = note
    data["fixtures"].append(entry)
    index_path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--development", action="store_true", help="required: invented data")
    parser.add_argument("--version", required=True, help="the released version, e.g. 1.0.0")
    parser.add_argument("--work", required=True, type=Path, help="a new folder named .data*")
    parser.add_argument("--note", help="a short note for index.json")
    args = parser.parse_args(argv)
    if not args.development:
        print("refused: run only with --development")
        return 2
    try:
        target = make(args.version, args.work, note=args.note)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    print(f"wrote {target} ({target.stat().st_size} bytes), its expected figures and index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
