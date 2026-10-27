"""Invented development data for the web UI and the end-to-end tests (M5, Q-22).

Creates users, accounts, assets with `ZZ` ISINs (no real country, no real security),
transactions and manual prices, all made up, through the public API in process. Market data
is always off: no outbound call. Two invented USD rates, dated the two days before the run, are
stored through the FX repository (no route stores rates), so "All accounts" values the dollar
account and fresh data shows no stale-rate notice.

Guards, so it can never touch a real installation: it runs only with `--development`, only
on a data directory given explicitly in `PT_DATA_DIR` whose name starts with `.data` (never
the default `/data` of the image), and refuses a database that already has a user or an
account. It is not part of the image.

    PT_DATA_DIR=.data-demo uv run python scripts/dev_data.py --development
    PT_DATA_DIR=.data-demo PT_COOKIE_SECURE=false PT_MARKET_DATA=false \
        uv run python -m portfolio_tracker

Start the app on the data with market data off, as above (the script prints that command):
market data is on by default, and a run would send the invented ISINs to the providers and
replace the invented rates with real ones.

`--reference` creates the reference size of the requirements instead (D-154): 10 users
(`ref01`...`ref10`), each with three accounts holding all of 200 invented securities, 5,000
transactions each over 20 years, sent through the CSV import (one file per user, preview then
confirm), and 20 years of weekday closes of every security and USD rates. No public route
stores provider closes, so the instruments, their listings (provider `dev-data`, which is no
engine), the link of each security and the closes are written through the repository layer:
development data only. With market data on, a run would send the 200 invented ISINs to
OpenFIGI and Yahoo, once and then again every retry interval (30 days), since the listing
search runs for every linked instrument, and would replace the invented USD rates; the
`dev-data` closes themselves are never fetched. Same guards, same start command.

    PT_DATA_DIR=.data-ref uv run python scripts/dev_data.py --development --reference
"""

import argparse
import base64
import csv
import io
import math
import os
import random
import sqlite3
import sys
import time
from array import array
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from portfolio_tracker.config import Settings
from portfolio_tracker.db import db_transaction
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.base import PriceClass, Rate
from portfolio_tracker.repositories import fx, instruments

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
USD_RATES = {2: "1.2400", 1: "1.2500"}  # days before the run (UTC): USD per EUR, invented
START = (
    "start the app on it, with market data off: "
    "PT_DATA_DIR={} PT_COOKIE_SECURE=false PT_MARKET_DATA=false uv run python -m portfolio_tracker"
)


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
    today = datetime.now(UTC).date()
    with db_transaction(client.app.state.engine, write=True) as session:
        fx.store_rates(
            session,
            [Rate(today - timedelta(days=n), "USD", Decimal(r)) for n, r in USD_RATES.items()],
            "dev-data",
        )
    return {
        "accounts": len(accounts),
        "assets": len(assets),
        "transactions": len(TRANSACTIONS),
        "prices": len(PRICES),
        "newcomer_password": newcomer["temporary_password"],
    }


# --- Reference size (D-154) ---------------------------------------------------------------

REF_PASSWORD = "dev-ref-password"
REF_SEED = 154
REF_PROVIDER = "dev-data"  # no engine has this name: its closes are never fetched
REF_EXCHANGE = "Borsa Esempio"
CENT = Decimal("0.01")
REF_ACCOUNTS = {  # key: (name, currency, deposit every n months, deposit amount)
    "main": ("Conto principale", "EUR", 1, Decimal(2000)),
    "second": ("Conto secondario", "EUR", 3, Decimal(3000)),
    "usd": ("Conto USA", "USD", 3, Decimal(3000)),
}
# Same-day replay order (domain-rules §4): the simulation follows it, so cash and quantities
# are never below zero at any point of the replay.
ORDER = {"DEPOSIT": 0, "BUY": 1, "DIVIDEND": 2, "COUPON": 2, "SELL": 3, "WITHDRAWAL": 4}


@dataclass(frozen=True)
class Scale:
    """The reference size of the requirements by default; the tests use a tiny one."""

    users: int = 10
    transactions: int = 5000  # per user
    securities: int = 200
    years: int = 20

    @property
    def bonds(self) -> int:
        return max(1, self.securities // 10)

    @property
    def stocks(self) -> int:
        return max(1, self.securities * 3 // 10)

    @property
    def shared(self) -> int:  # securities held in a second account too
        return max(1, self.securities * 3 // 20)


@dataclass(frozen=True)
class Security:
    index: int
    isin: str
    ticker: str
    name: str
    asset_type: str
    currency: str
    unit: str
    income: bool  # pays dividends or coupons


def weekdays(scale: Scale, today: date) -> list[date]:
    start = date(today.year - scale.years, today.month, min(today.day, 28))
    days = (start + timedelta(days=n) for n in range((today - start).days + 1))
    return [d for d in days if d.weekday() < 5]


def securities(scale: Scale) -> list[Security]:
    """Bonds first, then stocks, then ETFs; the last quarter of the stocks and the last eighth
    of the ETFs are quoted in USD."""
    result = []
    etfs = scale.securities - scale.bonds - scale.stocks
    usd_stocks, usd_etfs = max(1, scale.stocks // 4), max(1, etfs // 8)
    for i in range(scale.securities):
        n = i + 1
        if i < scale.bonds:
            kind, name, currency = "BOND", f"Esempio Obbligazione {n:03d}", "EUR"
        elif i < scale.bonds + scale.stocks:
            last = i >= scale.bonds + scale.stocks - usd_stocks
            kind, name, currency = "STOCK", f"Esempio Azioni {n:03d}", "USD" if last else "EUR"
        else:
            last = i >= scale.securities - usd_etfs
            kind, name, currency = "ETF", f"Esempio ETF {n:03d}", "USD" if last else "EUR"
        unit = "PERCENT_OF_NOMINAL" if kind == "BOND" else "CURRENCY"
        income = kind != "ETF" or i % 2 == 0  # half of the ETFs distribute
        result.append(
            Security(i, zz_isin(1001 + i), f"ZZR{n:03d}", name, kind, currency, unit, income)
        )
    return result


def walk(rng: random.Random, kind: str, length: int) -> array:
    """Invented daily closes with 4 decimals: a random walk, bonds kept near 100 %."""
    values = array("d")
    if kind == "BOND":
        price = rng.uniform(95, 105)
        for _ in range(length):
            price = min(120.0, max(80.0, price + (100 - price) * 0.01 + rng.gauss(0, 0.15)))
            values.append(round(price, 4))
        return values
    price = rng.uniform(20, 150) if kind == "ETF" else rng.uniform(5, 300)
    vol = 0.01 if kind == "ETF" else 0.018
    for _ in range(length):
        price = max(1.0, price * math.exp(rng.gauss(0.0002, vol)))
        values.append(round(price, 4))
    return values


def usd_rates(length: int) -> array:
    rng, rate, values = random.Random(REF_SEED * 7), 1.15, array("d")
    for _ in range(length):
        rate = min(1.45, max(0.95, rate + (1.15 - rate) * 0.005 + rng.gauss(0, 0.004)))
        values.append(round(rate, 4))
    return values


def dec(value: float) -> Decimal:
    return Decimal(f"{value:.4f}")


class Ledger:
    """One user's invented transactions, simulated in replay order (D-154)."""

    def __init__(self, scale, secs, days, closes, rates, user: int) -> None:
        self.rng = random.Random(REF_SEED * 1000 + user)
        self.secs, self.days, self.closes, self.rates = secs, days, closes, rates
        self.scale = scale

    def place(self) -> list[tuple[str, int]]:
        """(account, security) pairs: every security once, some in a second account."""
        pairs = []
        for sec in self.secs:
            if sec.currency == "USD":
                pairs.append(("usd", sec.index))
            else:
                pairs.append(("main" if self.rng.random() < 0.6 else "second", sec.index))
        home = dict((i, a) for a, i in pairs)
        second = {"main": "second", "second": "main", "usd": "main"}
        for i in self.rng.sample(range(len(self.secs)), self.scale.shared):
            pairs.append((second[home[i]], i))
        return pairs

    def plan(self) -> list[tuple[int, str, object]]:
        """(day index, type, pair or account) for exactly `scale.transactions` events."""
        n, rng, events = len(self.days), self.rng, []
        start, seen = self.days[0].year * 12 + self.days[0].month, set()
        for k, day in enumerate(self.days):  # deposits on the first weekday of their month
            month = day.year * 12 + day.month
            if month in seen:
                continue
            seen.add(month)
            for key, (_, _, every, _) in REF_ACCOUNTS.items():
                if (month - start) % every == 0:
                    events.append((k, "DEPOSIT", key))
        for k in range(130, n, 261):  # one withdrawal a year, mid-year
            events.append((k, "WITHDRAWAL", "main"))
        pairs = self.place()
        first = {}
        for pair in pairs:
            first[pair] = rng.randrange(0, n * 3 // 4)
            events.append((first[pair], "BUY", pair))
        paying = [p for p in pairs if self.secs[p[1]].income]
        total = self.scale.transactions
        income, sells = total * 9 // 50, total * 39 // 250
        extra = total - len(events) - income - sells
        if extra < 0:
            raise ValueError(f"{total} transactions are too few for this scale")
        for kind, count, pool in (("INCOME", income, paying), ("SELL", sells, pairs),
                                  ("BUY", extra, pairs)):  # fmt: skip
            for _ in range(count):
                pair = rng.choice(pool)
                events.append((rng.randrange(first[pair] + 1, n), kind, pair))
        return events

    def rows(self) -> list[dict[str, str]]:
        held: dict[tuple[str, int], Decimal] = {}
        cash = {key: Decimal(0) for key in REF_ACCOUNTS}
        deposits: dict[str, list[dict]] = {key: [] for key in REF_ACCOUNTS}
        coupon = {s.index: Decimal(self.rng.randint(10, 40)) / 10 for s in self.secs}
        out = []
        events = self.plan()
        events.sort(key=lambda e: (e[0], ORDER.get(e[1], 2)))
        for k, kind, target in events:
            day = self.days[k]
            if kind == "DEPOSIT":
                row = self.cash_row(target, day, "DEPOSIT", REF_ACCOUNTS[target][3])
                deposits[target].append(row)
                cash[target] += REF_ACCOUNTS[target][3]
                out.append(row)
                continue
            if kind == "WITHDRAWAL":
                amount = Decimal(self.rng.randint(1, 5) * 500)
                self.cover(cash, deposits, target, amount)
                cash[target] -= amount
                out.append(self.cash_row(target, day, "WITHDRAWAL", amount))
                continue
            account, i = target
            sec = self.secs[i]
            price = dec(self.closes[i][k])
            per_unit = price / 100 if sec.asset_type == "BOND" else price
            if sec.currency != REF_ACCOUNTS[account][1]:  # USD security in a EUR account
                per_unit = per_unit / dec(self.rates[k])
            have = held.get(target, Decimal(0))
            step = Decimal(1000) if sec.asset_type == "BOND" else Decimal(1)
            if kind == "SELL":
                quantity = (have * Decimal(self.rng.uniform(0.1, 0.6)) / step).to_integral_value(
                    ROUND_FLOOR
                ) * step
                quantity = min(quantity, have - step)
                if quantity < step:
                    kind = "BUY"  # too little to sell: the event becomes a buy
            if kind == "INCOME":
                if sec.asset_type == "BOND":
                    gross = (have * coupon[i] / 100).quantize(CENT)
                    rate, kind = Decimal("0.125"), "COUPON"
                else:
                    gross = (have * per_unit * Decimal(self.rng.uniform(0.005, 0.015))).quantize(
                        CENT
                    )
                    rate, kind = Decimal("0.26"), "DIVIDEND"
                gross = max(gross, CENT)
                taxes = (gross * rate).quantize(CENT)  # invented withholding, as a broker reports
                cash[account] += gross - taxes
                out.append(self.row(account, day, kind, sec, gross_amount=gross, taxes=taxes,
                                    total_amount=gross - taxes))  # fmt: skip
                continue
            fees = Decimal(self.rng.randint(100, 500)) / 100
            if kind == "BUY":
                budget = Decimal(self.rng.randint(500, 5000))
                quantity = max(
                    step, (budget / per_unit / step).to_integral_value(ROUND_FLOOR) * step
                )
                gross = (quantity * per_unit).quantize(CENT)
                accrued = (
                    (quantity * Decimal(self.rng.randint(0, 200)) / 10000).quantize(CENT)
                    if sec.asset_type == "BOND"
                    else None
                )
                total = gross + fees + (accrued or 0)
                self.cover(cash, deposits, account, total)
                cash[account] -= total
                held[target] = have + quantity
                out.append(self.row(account, day, "BUY", sec, quantity=quantity, unit_price=price,
                                    gross_amount=gross, fees=fees, accrued_interest_paid=accrued,
                                    total_amount=total))  # fmt: skip
                continue
            gross = (quantity * per_unit).quantize(CENT)
            taxes = (gross / 100).quantize(CENT)  # invented, as a broker reports
            total = gross - fees - taxes
            cash[account] += total
            held[target] = have - quantity
            amounts = {"gross_amount": gross, "fees": fees, "taxes": taxes, "total_amount": total}
            out.append(self.row(account, day, "SELL", sec, quantity=quantity, unit_price=price,
                                **amounts))  # fmt: skip
        return out

    @staticmethod
    def cover(cash, deposits, account: str, amount: Decimal) -> None:
        """Raise the account's latest deposit so that `amount` leaves at least 0 in cash."""
        short = amount - cash[account]
        if short > 0:
            top_up = (short / 1000).to_integral_value(ROUND_FLOOR) * 1000 + 1000
            latest = deposits[account][-1]
            latest["total_amount"] = str(Decimal(latest["total_amount"]) + top_up)
            cash[account] += top_up

    @staticmethod
    def cash_row(account: str, day: date, kind: str, amount: Decimal) -> dict[str, str]:
        name, currency, _, _ = REF_ACCOUNTS[account]
        return {"account": name, "account_currency": currency, "account_provider":
                "Broker Esempio", "trade_date": day.isoformat(), "type": kind,
                "total_amount": str(amount)}  # fmt: skip

    @staticmethod
    def row(account: str, day: date, kind: str, sec: Security, **amounts) -> dict[str, str]:
        row = Ledger.cash_row(account, day, kind, Decimal(0))
        row.update({"isin": sec.isin, "ticker": sec.ticker, "asset_name": sec.name,
                    "asset_type": sec.asset_type, "asset_quote_currency": sec.currency,
                    "asset_quote_unit": sec.unit})  # fmt: skip
        row.update({k: str(v) for k, v in amounts.items() if v is not None})
        return row


def ledger_csv(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["format_version", *COLUMNS], lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({"format_version": "1", **row})
    return buffer.getvalue().encode()


COLUMNS = ("account", "account_currency", "account_provider", "trade_date", "type", "isin",
           "ticker", "asset_name", "asset_type", "asset_quote_currency", "asset_quote_unit",
           "quantity", "unit_price", "gross_amount", "fees", "accrued_interest_paid", "taxes",
           "total_amount")  # fmt: skip


def store_market_data(engine, secs, days, closes, rates) -> int:
    """NOT A PUBLIC PATH (D-154): no route stores provider closes, so the instruments, their
    `dev-data` listings and 20 years of closes and USD rates are written through the
    repository layer, as a provider run would. Development data only."""
    stored = 0
    for sec in secs:
        with db_transaction(engine, write=True) as session:
            instrument = instruments.get_or_create_instrument(session, sec.isin, sec.currency)
            kind = PriceClass.BOND if sec.asset_type == "BOND" else PriceClass.ETF_STOCK
            instruments.set_price_class(session, instrument, kind, REF_PROVIDER)
            listing = instruments.save_listing(
                session, instrument.id, REF_PROVIDER, sec.ticker, exchange=REF_EXCHANGE,
                currency=sec.currency, unit=sec.unit, matched_by="ISIN", status="OK",
                last_success_at=datetime.now(UTC),
            )  # fmt: skip
            instruments.activate(session, listing)
            stored += instruments.store_closes(
                session,
                listing.id,
                (
                    instruments.StoredClose(d, dec(c), None)
                    for d, c in zip(days, closes[sec.index], strict=True)
                ),
            )
    with db_transaction(engine, write=True) as session:
        fx.store_rates(
            session,
            [Rate(d, "USD", dec(r)) for d, r in zip(days, rates, strict=True)],
            REF_PROVIDER,
        )
    return stored


def link_reference_assets(engine) -> int:
    """NOT A PUBLIC PATH (D-154): link every imported security to its instrument, as the
    market data job would with market data on."""
    with db_transaction(engine, write=True) as session:
        assets = instruments.assets_to_link(session)
        for asset in assets:
            instrument = instruments.get_instrument(session, asset.isin, asset.quote_currency)
            assert instrument is not None
            instruments.link_asset(session, asset.id, instrument.id)
        return len(assets)


def seed_reference(client: TestClient, scale: Scale) -> dict:
    today = datetime.now(UTC).date()
    days = weekdays(scale, today)
    secs = securities(scale)
    rng = random.Random(REF_SEED)
    closes = [walk(rng, sec.asset_type, len(days)) for sec in secs]
    rates = usd_rates(len(days))
    closes_stored = store_market_data(client.app.state.engine, secs, days, closes, rates)

    _call(client, "POST", "/setup/admin",
          {"username": ADMIN[0], "password": ADMIN[1], "language": "it"})  # fmt: skip
    created = [
        _call(client, "POST", "/admin/users", {"username": f"ref{u:02d}", "language": "it"})
        for u in range(1, scale.users + 1)
    ]
    _call(client, "POST", "/auth/logout")
    transactions = 0
    for u, user in enumerate(created, start=1):
        _call(client, "POST", "/auth/login",
              {"username": f"ref{u:02d}", "password": user["temporary_password"]})  # fmt: skip
        password = {"current_password": user["temporary_password"], "new_password": REF_PASSWORD}
        _call(client, "POST", "/me/password", password)
        rows = Ledger(scale, secs, days, closes, rates, u).rows()
        body = {
            "content_base64": base64.b64encode(ledger_csv(rows)).decode(),
            "file_name": f"ref{u:02d}.csv",
            "accounts": {name: None for name, *_ in REF_ACCOUNTS.values()},
        }
        preview = _call(client, "POST", "/imports/preview", body)
        selected = [line["key"] for line in preview["lines"] if line["selectable"]]
        if preview["blocking"] or len(selected) != len(rows):
            raise RuntimeError(
                f"ref{u:02d}: the import preview refused rows: {preview['blocking']}"
            )
        body |= {"fingerprint": preview["fingerprint"], "selected": selected}
        confirmed = _call(client, "POST", "/imports/confirm", body)
        transactions += len(confirmed["transactions"])
        _call(client, "POST", "/auth/logout")
    linked = link_reference_assets(client.app.state.engine)
    return {"users": len(created), "transactions": transactions, "securities": len(secs),
            "closes": closes_stored, "linked": linked}  # fmt: skip


def main(
    argv: list[str] | None = None, settings: Settings | None = None, scale: Scale | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--development",
        action="store_true",
        help="required: confirms this is a development data directory",
    )
    parser.add_argument(
        "--reference",
        action="store_true",
        help="the reference size of the requirements instead of the demo data (D-154)",
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
    if args.reference:
        started = time.monotonic()
        with TestClient(create_app(settings), base_url="https://localhost") as client:
            created = seed_reference(client, scale or Scale())
        print(f"data directory: {settings.data_dir}")
        print(
            f"created {created['users']} users, {created['transactions']} transactions, "
            f"{created['securities']} securities ({created['linked']} linked), "
            f"{created['closes']} closes in {time.monotonic() - started:.0f} s"
        )
        print(f"log in as {ADMIN[0]} / {ADMIN[1]} (Administrator), "
              f"ref01...ref{created['users']:02d} / {REF_PASSWORD} (investors)")  # fmt: skip
        print(START.format(settings.data_dir))
        return 0
    with TestClient(create_app(settings), base_url="https://localhost") as client:
        created = seed(client)
    print(f"data directory: {settings.data_dir}")
    print(
        f"created {created['accounts']} accounts, {created['assets']} assets, "
        f"{created['transactions']} transactions, {created['prices']} manual prices"
    )
    print(f"log in as {ADMIN[0]} / {ADMIN[1]} (Administrator), {DEMO[0]} / {DEMO[1]} (investor)")
    print(f"{NEWCOMER} has the temporary password {created['newcomer_password']}")
    print(START.format(settings.data_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
