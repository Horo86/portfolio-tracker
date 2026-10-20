"""Invented development data for the web UI and the end-to-end tests (M5, Q-22).

Creates users, accounts, assets with `ZZ` ISINs (no real country, no real security),
transactions and manual prices, all made up, through the public API in process. Market data
is always off: no outbound call.

Guards, so it can never touch a real installation: it runs only with `--development`, only
on a data directory given explicitly in `PT_DATA_DIR` whose name starts with `.data` (never
the default `/data` of the image), and refuses a database that already has a user or an
account. It is not part of the image.

    PT_DATA_DIR=.data-demo uv run python scripts/dev_data.py --development
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

CSRF = {"X-Requested-With": "portfolio-tracker"}
ADMIN = ("admin", "dev-admin-password")
DEMO = ("demo", "dev-demo-password")
NEWCOMER = "newcomer"


class Refused(Exception):
    pass


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
    "world": {"name": "Esempio Azionario Mondo ETF", "asset_type": "ETF", "ticker": "ZZWLD",
              "quote_currency": "EUR", "quote_unit": "CURRENCY", "isin": zz_isin(101)},
    "bonds": {"name": "Esempio Obbligazionario Euro ETF", "asset_type": "ETF", "ticker": "ZZEUB",
              "quote_currency": "EUR", "quote_unit": "CURRENCY", "isin": zz_isin(102)},
    "stock": {"name": "Esempio Industrie SpA", "asset_type": "STOCK", "ticker": "ZZIND",
              "quote_currency": "EUR", "quote_unit": "CURRENCY", "isin": zz_isin(103)},
    "btp": {"name": "Esempio Titolo di Stato 2031", "asset_type": "BOND",
            "quote_currency": "EUR", "quote_unit": "PERCENT_OF_NOMINAL", "isin": zz_isin(104)},
    "robotics": {"name": "Example Robotics Inc.", "asset_type": "STOCK", "ticker": "ZZROB",
                 "quote_currency": "USD", "quote_unit": "CURRENCY", "isin": zz_isin(105)},
}  # fmt: skip

# (account, type, day, asset, fields): every amount is invented.
TRANSACTIONS = [
    ("eur", "DEPOSIT", "2025-01-02", None, {"total_amount": "20000.00"}),
    ("eur", "BUY", "2025-01-10", "world", {"quantity": "50", "unit_price": "100.00",
        "gross_amount": "5000.00", "fees": "2.50", "total_amount": "5002.50"}),
    ("eur", "BUY", "2025-01-10", "bonds", {"quantity": "40", "unit_price": "50.00",
        "gross_amount": "2000.00", "fees": "2.50", "total_amount": "2002.50"}),
    ("eur", "BUY", "2025-02-03", "stock", {"quantity": "100", "unit_price": "12.00",
        "gross_amount": "1200.00", "fees": "2.00", "total_amount": "1202.00"}),
    ("eur", "BUY", "2025-02-17", "btp", {"quantity": "5000", "unit_price": "98.00",
        "gross_amount": "4900.00", "fees": "3.00", "accrued_interest_paid": "20.00",
        "taxes": "-2.50", "total_amount": "4920.50"}),
    ("eur", "SELL", "2025-06-12", "stock", {"quantity": "40", "unit_price": "14.00",
        "gross_amount": "560.00", "fees": "2.00", "taxes": "20.07", "total_amount": "537.93"}),
    ("eur", "DIVIDEND", "2025-05-20", "stock", {"gross_amount": "30.00", "taxes": "7.80",
        "total_amount": "22.20"}),
    ("eur", "COUPON", "2025-08-01", "btp", {"gross_amount": "75.00", "taxes": "9.38",
        "total_amount": "65.62"}),
    ("usd", "DEPOSIT", "2025-03-03", None, {"total_amount": "10000.00"}),
    ("usd", "BUY", "2025-03-05", "robotics", {"quantity": "20", "unit_price": "150.00",
        "gross_amount": "3000.00", "fees": "1.00", "total_amount": "3001.00"}),
]  # fmt: skip

PRICES = {"world": "108.40", "bonds": "49.10", "stock": "13.25", "btp": "99.15",
          "robotics": "162.50"}  # fmt: skip
PRICE_DAY = "2025-12-30"


def check_empty(db_path: Path) -> None:
    """Refuse a database with any user or account, without migrating or changing it."""
    if not db_path.exists():
        return
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        for table in ("users", "accounts"):
            if table in tables and db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise Refused(f"the database {db_path} is not empty ({table}): nothing done")


def _call(client: TestClient, method: str, path: str, body: dict | None = None) -> dict:
    response = client.request(method, f"/api/v1{path}", json=body, headers=CSRF)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
    return response.json() if response.content else {}


def seed(client: TestClient) -> dict:
    """Create the invented data. Returns what was created, for the summary and the tests."""
    _call(client, "POST", "/setup/admin",
          {"username": ADMIN[0], "password": ADMIN[1], "language": "it"})  # fmt: skip
    demo = _call(client, "POST", "/admin/users",
                 {"username": DEMO[0], "display_name": "Demo", "language": "it"})  # fmt: skip
    newcomer = _call(client, "POST", "/admin/users", {"username": NEWCOMER, "language": "en"})
    _call(client, "POST", "/auth/logout")

    _call(client, "POST", "/auth/login",
          {"username": DEMO[0], "password": demo["temporary_password"]})  # fmt: skip
    _call(client, "POST", "/me/password",
          {"current_password": demo["temporary_password"], "new_password": DEMO[1]})  # fmt: skip
    accounts = {
        "eur": _call(client, "POST", "/accounts",
                     {"name": "Conto titoli", "currency": "EUR", "provider": "Broker Esempio"}),
        "usd": _call(client, "POST", "/accounts",
                     {"name": "Conto estero", "currency": "USD", "provider": "Broker Esempio"}),
    }  # fmt: skip
    assets = {key: _call(client, "POST", "/assets", body) for key, body in ASSETS.items()}
    for account, type_, day, asset, fields in TRANSACTIONS:
        body = {"account_id": accounts[account]["id"], "type": type_, "trade_date": day}
        if asset:
            body["asset_id"] = assets[asset]["id"]
        _call(client, "POST", "/transactions", body | fields)
    for key, close in PRICES.items():
        _call(client, "PUT", f"/assets/{assets[key]['id']}/prices",
              {"day": PRICE_DAY, "close": close})  # fmt: skip
    _call(client, "POST", "/auth/logout")
    return {
        "accounts": len(accounts),
        "assets": len(assets),
        "transactions": len(TRANSACTIONS),
        "prices": len(PRICES),
        "newcomer_password": newcomer["temporary_password"],
    }


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--development",
        action="store_true",
        help="required: confirms this is a development data directory",
    )
    args = parser.parse_args(argv)
    if not args.development:
        print("refused: run only with --development, on a development data directory")
        return 2
    if settings is None and not os.environ.get("PT_DATA_DIR"):
        print("refused: set PT_DATA_DIR to a development data directory, e.g. .data-demo")
        return 2
    settings = (settings or Settings()).model_copy(update={"market_data": False})
    if not settings.data_dir.name.startswith(".data"):
        print(
            f"refused: {settings.data_dir} is not a development data directory "
            "(its name must start with .data)"
        )
        return 2
    try:
        check_empty(settings.db_path)
    except Refused as exc:
        print(f"refused: {exc}")
        return 2
    # http + Secure cookies would be dropped by the client: same as the tests, use https.
    with TestClient(create_app(settings), base_url="https://localhost") as client:
        created = seed(client)
    print(f"data directory: {settings.data_dir}")
    print(
        f"created {created['accounts']} accounts, {created['assets']} assets, "
        f"{created['transactions']} transactions, {created['prices']} manual prices"
    )
    print(f"log in as {ADMIN[0]} / {ADMIN[1]} (Administrator), {DEMO[0]} / {DEMO[1]} (investor)")
    print(f"{NEWCOMER} has the temporary password {created['newcomer_password']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
