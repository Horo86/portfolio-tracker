"""App CSV export and the export -> import round trip (D-38, D-107, D-110): importing an export
into a fresh account rebuilds the same cash and positions; importing it back into the same
user imports nothing. Synthetic fixture and invented data only."""

import base64
import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import create_admin, fake_isin, investor

V1 = "/api/v1"
FIXTURES = Path(__file__).parents[2] / "tests/fixtures/directa"
XLSX = (FIXTURES / "directa_synthetic_2024.xlsx").read_bytes()
CHOICES = {"isin:IE0TEST00020": {"asset_type": "ETF"}, "isin:IT0TEST00015": {"asset_type": "STOCK"}}


def call(client: TestClient, method: str, path: str, who: dict, body=None, expected=200):
    response = client.request(method, f"{V1}{path}", headers=who, json=body)
    assert response.status_code == expected, response.text
    return response


def ok(client, method, path, who, body=None, expected=200) -> dict:
    return call(client, method, path, who, body, expected).json()


def imported(client, who: dict, body: dict, *, tick: tuple[str, ...] = ()) -> dict:
    seen = ok(client, "POST", "/imports/preview", who, body)
    assert seen["blocking"] == [], seen["blocking"]
    chosen = [x["key"] for x in seen["lines"] if x["selected"]] + list(tick)
    confirm = body | {"fingerprint": seen["fingerprint"], "selected": chosen}
    return ok(client, "POST", "/imports/confirm", who, confirm, 201)


def export(client, who: dict, account_id: str | None = None) -> str:
    query = f"?account_id={account_id}" if account_id else ""
    response = call(client, "GET", f"/transactions/export.csv{query}", who)
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "attachment" in response.headers["content-disposition"]
    return response.text


def asset_id(client, who: dict, isin: str) -> str:
    return next(a["id"] for a in ok(client, "GET", "/assets", who) if a["isin"] == isin)


def holdings(client, who: dict, account_id: str) -> dict:
    """Cash and every position figure, by ISIN (ids differ between users)."""
    summary = ok(client, "GET", f"/accounts/{account_id}/summary", who)
    positions = ok(client, "GET", f"/accounts/{account_id}/positions", who)
    by_isin = {p["isin"]: {k: v for k, v in p.items() if k != "asset_id"} for p in positions}
    figures = {k: v for k, v in summary.items() if k not in ("account_id", "day", "warnings")}
    return {"summary": figures, "positions": by_isin}


def rows(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


@pytest.fixture
def users(client: TestClient) -> dict:
    create_admin(client)
    client.cookies.clear()
    return {"alice": investor(client, "alice"), "bob": investor(client, "bob")}


@pytest.fixture
def alice_data(client: TestClient, users) -> dict:
    """The Directa fixture (the order to review ticked) plus hand-entered transactions: a
    timed sale with a formula-like note, a split, and a USD account with a GBX stock."""
    alice = users["alice"]
    eur = ok(client, "POST", "/accounts", alice, {"name": "Directa", "currency": "EUR"}, 201)
    body = {"content_base64": base64.b64encode(XLSX).decode(), "account_id": eur["id"],
            "assets": CHOICES}  # fmt: skip
    imported(client, alice, body, tick=("order:T0000000000004",))
    etf, stock = asset_id(client, alice, "IE0TEST00020"), asset_id(client, alice, "IT0TEST00015")
    for tx in (
        {"type": "SELL", "asset_id": etf, "trade_date": "2024-10-02", "trade_time": "10:30",
         "quantity": "5", "unit_price": "105.00", "gross_amount": "525.00", "fees": "1.50",
         "taxes": "2.00", "total_amount": "521.50", "notes": "=1+1 check", "metadata": {"k": 1}},
        {"type": "STOCK_SPLIT", "asset_id": stock, "trade_date": "2024-10-03",
         "split_ratio": "2:1"},
    ):  # fmt: skip
        ok(client, "POST", "/transactions", alice, tx | {"account_id": eur["id"]}, 201)
    usd = ok(client, "POST", "/accounts", alice, {"name": "Dollars", "currency": "USD",
                                                  "provider": "+Broker"}, 201)  # fmt: skip
    gbx = ok(client, "POST", "/assets", alice, {"name": "Example plc", "asset_type": "STOCK",
             "quote_currency": "GBP", "quote_unit": "MINOR_CURRENCY_UNIT",
             "isin": fake_isin(77), "ticker": "EXP"}, 201)  # fmt: skip
    for tx in (
        {"type": "DEPOSIT", "trade_date": "2024-02-01", "total_amount": "1000.00"},
        {"type": "BUY", "asset_id": gbx["id"], "trade_date": "2024-02-02", "quantity": "10",
         "unit_price": "250", "gross_amount": "31.75", "fees": "0.25", "total_amount": "32.00",
         "affects_cash": False},
    ):  # fmt: skip
        ok(client, "POST", "/transactions", alice, tx | {"account_id": usd["id"]}, 201)
    return {"eur": eur["id"], "usd": usd["id"]}


def test_export_has_every_column_in_replay_order(client: TestClient, users, alice_data) -> None:
    text = export(client, users["alice"])
    lines = rows(text)
    assert text.splitlines()[0].startswith("format_version,id,account,")
    assert [r["account"] for r in lines] == ["Directa"] * 14 + ["Dollars"] * 2  # by name
    directa = [r for r in lines if r["account"] == "Directa"]
    assert [r["trade_date"] for r in directa] == sorted(r["trade_date"] for r in directa)
    sale = next(r for r in lines if r["type"] == "SELL")
    expected = ("'=1+1 check", "10:30", '{"k":1}')  # the note is protected (D-110)
    assert (sale["notes"], sale["trade_time"], sale["metadata"]) == expected
    assert next(r for r in lines if r["account"] == "Dollars")["account_provider"] == "'+Broker"
    split = next(r for r in lines if r["type"] == "STOCK_SPLIT")
    assert (split["split_ratio"], split["affects_cash"]) == ("2:1", "")
    expense = next(r for r in lines if r["notes"] == "Tobin tax italia")
    assert json.loads(expense["metadata"]) == {"original_type": "Tobin tax italia"}  # D-13
    assert {r["format_version"] for r in lines} == {"1"}
    only = rows(export(client, users["alice"], alice_data["usd"]))
    assert [r["type"] for r in only] == ["DEPOSIT", "BUY"]


def test_round_trip_into_a_fresh_account(client: TestClient, users, alice_data) -> None:
    alice, bob = users["alice"], users["bob"]
    text = export(client, alice)
    fresh = ok(client, "POST", "/accounts", bob, {"name": "Directa", "currency": "EUR"}, 201)
    body = {"content_base64": base64.b64encode(text.encode()).decode(), "file_name": "x.csv",
            "accounts": {"Directa": fresh["id"], "Dollars": None}}  # fmt: skip
    done = imported(client, bob, body)
    assert len(done["transactions"]) == len(rows(text)) == 16
    assert len(done["batches"]) == 2 and len(done["accounts_created"]) == 1

    usd = next(a["id"] for a in ok(client, "GET", "/accounts", bob) if a["name"] == "Dollars")
    original = holdings(client, alice, alice_data["eur"])
    assert len(original["positions"]) == 3 and original["summary"]["realized_gross"] != "0"
    assert holdings(client, bob, fresh["id"]) == original
    assert holdings(client, bob, usd) == holdings(client, alice, alice_data["usd"])

    # Bob's export is Alice's but for the ids: another user's ids are replaced (D-38).
    def without_ids(t: str) -> list[dict]:
        return [{k: v for k, v in r.items() if k != "id"} for r in rows(t)]

    mine = export(client, bob)
    assert without_ids(mine) == without_ids(text)
    assert not {r["id"] for r in rows(mine)} & {r["id"] for r in rows(text)}


def test_reimporting_an_export_imports_nothing(client: TestClient, users, alice_data) -> None:
    alice = users["alice"]
    text = export(client, alice)
    body = {"content_base64": base64.b64encode(text.encode()).decode()}
    seen = ok(client, "POST", "/imports/preview", alice, body)
    assert [a["account_id"] for a in seen["accounts"]] == [alice_data["eur"], alice_data["usd"]]
    assert seen["counts"]["duplicates"] == 16 and seen["counts"]["selected"] == 0
    done = imported(client, alice, body)
    assert done["transactions"] == [] and [b["duplicates"] for b in done["batches"]] == [14, 2]
    assert export(client, alice) == text


def test_export_of_another_users_account_is_not_found(client: TestClient, users, alice_data):
    response = call(client, "GET", f"/transactions/export.csv?account_id={alice_data['eur']}",
                    users["bob"], expected=404)  # fmt: skip
    assert response.json()["code"] == "NOT_FOUND"
    assert rows(export(client, users["bob"])) == []  # Bob has nothing: header only
