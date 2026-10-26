"""Derived reads: account summary, positions and portfolio summary (domain-rules §5, §6, §9,
§12, §15; E3, E4, E9 through the API). The test clock says 2026-01-15 (UTC). Invented data only.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Asset as AssetRow
from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.model import (
    Asset,
    AssetType,
    QuoteUnit,
    Transaction,
    TransactionType,
)
from portfolio_tracker.engine.valuation import FxRates, PriceHistory, value_account

ETF = {"name": "Example ETF", "asset_type": "ETF", "quote_currency": "EUR",
       "quote_unit": "CURRENCY"}  # fmt: skip
BOND = {"name": "Example Bond 2030", "asset_type": "BOND", "quote_currency": "EUR",
        "quote_unit": "PERCENT_OF_NOMINAL"}  # fmt: skip
US_STOCK = {"name": "Example US Corp", "asset_type": "STOCK", "quote_currency": "USD",
            "quote_unit": "CURRENCY"}  # fmt: skip


def post(client, path, body, headers=CSRF, expected=201) -> dict:
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def tx(client, account, type_, day, headers=CSRF, **fields) -> dict:
    body = {"account_id": account["id"], "type": type_, "trade_date": day} | fields
    return post(client, "/api/v1/transactions", body, headers)["transactions"][0]


def price(client, asset, day, close, headers=CSRF) -> None:
    response = client.put(
        f"/api/v1/assets/{asset['id']}/prices", headers=headers, json={"day": day, "close": close}
    )
    assert response.status_code == 200, response.text


def get(client, path, headers=None, **params):
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def dec(body: dict, *names: str) -> tuple:
    """Figures as Decimals (the API keeps the scale: `60.00`, `0.10000000`)."""
    return tuple(None if body[n] is None else Decimal(body[n]) for n in names)


@pytest.fixture
def account(client: TestClient) -> dict:
    create_admin(client)
    return post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})


def e3(client, account) -> dict:
    """Example E3 with a deposit first, valued at a manual price of 110.00."""
    etf = post(client, "/api/v1/assets", ETF | {"isin": fake_isin(1)})
    tx(client, account, "DEPOSIT", "2025-01-02", total_amount="1000.00")
    tx(client, account, "BUY", "2025-01-10", asset_id=etf["id"], quantity="10",
       unit_price="100.00", gross_amount="1000.00", total_amount="1000.00")  # fmt: skip
    tx(client, account, "SELL", "2025-03-10", asset_id=etf["id"], quantity="4",
       unit_price="120.00", gross_amount="480.00", taxes="20.80",
       total_amount="459.20")  # fmt: skip
    tx(client, account, "DIVIDEND", "2025-05-02", asset_id=etf["id"], gross_amount="20.00",
       taxes="5.20", total_amount="14.80")  # fmt: skip
    price(client, etf, "2025-12-30", "110.00")
    return etf


# --- worked examples --------------------------------------------------------------------


def test_example_e3_account_summary_and_position(client: TestClient, account) -> None:
    e3(client, account)
    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")

    assert summary["currency"] == "EUR" and summary["day"] == "2026-01-15"
    assert dec(summary, "unrealized", "unrealized_pct") == (Decimal(60), Decimal("0.1"))
    assert dec(summary, "realized_gross", "realized_taxes", "realized_net") == (
        Decimal(80), Decimal("20.80"), Decimal("59.20"),
    )  # fmt: skip
    assert dec(summary, "realized_pct_gross", "realized_pct_net") == (
        Decimal("0.2"), Decimal("0.148"),
    )  # fmt: skip
    assert dec(summary, "income_gross", "income_taxes", "income_net") == (
        Decimal(20), Decimal("5.20"), Decimal("14.80"),
    )  # fmt: skip
    assert dec(summary, "gain_gross", "taxes", "gain_net") == (
        Decimal(160), Decimal(26), Decimal(134),
    )  # fmt: skip
    # cash 1000.00 - 1000.00 + 459.20 + 14.80; value = 6 x 110.00 + cash
    assert dec(summary, "cash", "securities_value", "total_value", "invested_capital") == (
        Decimal("474.00"), Decimal(660), Decimal("1134.00"), Decimal(600),
    )  # fmt: skip
    assert summary["open_positions"] == 1
    assert summary["partial"] is False and summary["warnings"] == []

    [position] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert position["open"] is True and position["name"] == "Example ETF"
    assert dec(position, "quantity", "pmc", "price", "value") == (
        Decimal(6), Decimal(100), Decimal(110), Decimal(660),
    )  # fmt: skip
    assert position["price_date"] == "2025-12-30" and position["estimated_at_cost"] is False
    assert dec(position, "gain_gross", "gain_net") == (Decimal(160), Decimal(134))


def test_examples_e4_e9_bond(client: TestClient, account) -> None:
    bond = post(client, "/api/v1/assets", BOND | {"isin": fake_isin(2)})
    tx(client, account, "BUY", "2025-02-03", asset_id=bond["id"], quantity="5000",
       unit_price="97.50", gross_amount="4875.00", fees="5.00", accrued_interest_paid="12.34",
       taxes="-2.44", total_amount="4889.90")  # fmt: skip
    price(client, bond, "2025-02-03", "98.20")
    positions = f"/api/v1/accounts/{account['id']}/positions"

    [p] = get(client, positions)
    assert dec(p, "pmc", "cost_basis", "value", "unrealized") == (
        Decimal("97.6"), Decimal(4880), Decimal(4910), Decimal(30),
    )  # fmt: skip
    assert Decimal(p["unrealized_pct"]) == Decimal("0.00614754")  # 30 / 4880, 8 decimals

    tx(client, account, "COUPON", "2025-08-01", asset_id=bond["id"], gross_amount="50.00",
       taxes="6.25", total_amount="43.75")  # fmt: skip
    tx(client, account, "SELL", "2025-09-15", asset_id=bond["id"], quantity="2000",
       unit_price="99.00", gross_amount="1980.00", fees="3.00", accrued_interest_received="8.00",
       taxes="3.13", total_amount="1981.87")  # fmt: skip

    [p] = get(client, positions)
    assert dec(p, "quantity", "pmc", "cost_basis") == (
        Decimal(3000),
        Decimal("97.6"),
        Decimal(2928),
    )
    assert dec(p, "realized_gross", "realized_taxes", "realized_net") == (
        Decimal(25), Decimal("3.13"), Decimal("21.87"),
    )  # fmt: skip
    assert dec(p, "income_gross", "income_taxes", "income_net") == (
        Decimal("45.66"), Decimal("3.81"), Decimal("41.85"),
    )  # fmt: skip
    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert Decimal(summary["cash"]) == Decimal("-2864.28")  # E9: cash of the asset since E4
    assert [w["code"] for w in summary["warnings"]] == ["NEGATIVE_CASH"]


# --- missing data -----------------------------------------------------------------------


def test_no_price_values_the_position_at_cost(client: TestClient, account) -> None:
    etf = post(client, "/api/v1/assets", ETF | {"isin": fake_isin(3)})
    tx(client, account, "BUY", "2025-01-10", asset_id=etf["id"], quantity="10",
       unit_price="10.00", gross_amount="100.00", fees="1.00", total_amount="101.00",
       affects_cash=False)  # fmt: skip
    price(client, etf, "2025-01-09", "99.00")  # before the BUY, still on or before today
    price(client, etf, "2026-01-16", "50.00")  # tomorrow (the latest day D-149 allows): ignored

    [p] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert p["price_date"] == "2025-01-09" and Decimal(p["value"]) == Decimal(990)

    other = post(client, "/api/v1/assets", ETF | {"name": "Unpriced ETF", "isin": fake_isin(4)})
    tx(client, account, "BUY", "2025-01-10", asset_id=other["id"], quantity="5",
       unit_price="10.00", gross_amount="50.00", total_amount="50.00",
       affects_cash=False)  # fmt: skip
    unpriced = get(client, f"/api/v1/accounts/{account['id']}/positions")[1]
    assert unpriced["name"] == "Unpriced ETF" and unpriced["estimated_at_cost"] is True
    assert (unpriced["price"], unpriced["price_date"]) == (None, None)
    assert dec(unpriced, "value", "unrealized", "gain_gross") == (Decimal(50), 0, 0)
    assert get(client, f"/api/v1/accounts/{account['id']}/summary")["partial"] is False


def test_a_foreign_quote_without_fx_makes_the_account_partial(client: TestClient, account) -> None:
    us = post(client, "/api/v1/assets", US_STOCK | {"isin": fake_isin(5)})
    etf = e3(client, account)
    tx(client, account, "BUY", "2025-04-01", asset_id=us["id"], quantity="2",
       unit_price="50.00", gross_amount="90.00", total_amount="90.00")  # fmt: skip
    price(client, us, "2025-12-30", "60.00")

    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert summary["partial"] is True
    # The USD position is left out of the value and of the unrealized base (D-64, D-73).
    assert dec(summary, "securities_value", "valued_cost", "invested_capital", "unrealized") == (
        Decimal(660), Decimal(600), Decimal(690), Decimal(60),
    )  # fmt: skip
    rows = {p["asset_id"]: p for p in get(client, f"/api/v1/accounts/{account['id']}/positions")}
    foreign = rows[us["id"]]
    assert foreign["value_unavailable"] is True
    assert [foreign[k] for k in ("value", "unrealized", "gain_gross", "gain_net")] == [None] * 4
    assert foreign["price"] == "60.00"  # the close is known, only the rate is missing
    assert rows[etf["id"]]["value_unavailable"] is False


def test_portfolio_leaves_out_an_account_without_a_rate(client: TestClient, account) -> None:
    e3(client, account)
    usd = post(client, "/api/v1/accounts", {"name": "US", "currency": "USD"})
    tx(client, usd, "DEPOSIT", "2025-01-02", total_amount="500.00")

    summary = get(client, "/api/v1/portfolio/summary")
    assert summary["currency"] == "EUR" and summary["account_count"] == 2
    assert summary["partial"] is True and summary["unavailable_currencies"] == ["USD"]
    account_summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    for name in ("total_value", "cash", "unrealized", "realized_net", "gain_net"):
        assert Decimal(summary[name]) == Decimal(account_summary[name]), name


def test_reporting_currency_follows_the_user_setting(client: TestClient, account) -> None:
    e3(client, account)
    response = client.patch("/api/v1/me", headers=CSRF, json={"reporting_currency": "USD"})
    assert response.status_code == 200, response.text

    summary = get(client, "/api/v1/portfolio/summary")
    assert summary["currency"] == "USD" and summary["unavailable_currencies"] == ["EUR"]
    assert Decimal(summary["total_value"]) == 0 and summary["partial"] is True


def test_empty_portfolio(client: TestClient) -> None:
    create_admin(client)
    summary = get(client, "/api/v1/portfolio/summary")
    assert summary["account_count"] == 0 and summary["partial"] is False
    assert Decimal(summary["total_value"]) == 0 and summary["unrealized_pct"] is None
    assert summary["day"] == "2026-01-15"


# --- valuation day ----------------------------------------------------------------------


def test_a_future_dated_transaction_counts_and_is_flagged(client: TestClient, account) -> None:
    tx(client, account, "DEPOSIT", "2026-01-10", total_amount="100.00")
    future = tx(client, account, "DEPOSIT", "2027-01-16", total_amount="5.00")

    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert summary["day"] == "2027-01-16" and Decimal(summary["cash"]) == Decimal("105.00")
    assert summary["warnings"] == [
        {"code": "FUTURE_DATE", "date": "2027-01-16", "transaction_id": future["id"]}
    ]
    assert get(client, "/api/v1/portfolio/summary")["day"] == "2027-01-16"


def test_closed_positions_only_on_request(client: TestClient, account) -> None:
    etf = post(client, "/api/v1/assets", ETF | {"isin": fake_isin(6)})
    tx(client, account, "BUY", "2025-01-10", asset_id=etf["id"], quantity="10",
       unit_price="10.00", gross_amount="100.00", total_amount="100.00")  # fmt: skip
    tx(client, account, "SELL", "2025-02-10", asset_id=etf["id"], quantity="10",
       unit_price="12.00", gross_amount="120.00", total_amount="120.00")  # fmt: skip
    path = f"/api/v1/accounts/{account['id']}/positions"

    assert get(client, path) == []
    [closed] = get(client, path, include_closed=True)
    assert closed["open"] is False and closed["pmc"] is None and closed["price"] is None
    assert dec(closed, "quantity", "value", "realized_gross", "gain_gross") == (0, 0, 20, 20)
    assert get(client, f"/api/v1/accounts/{account['id']}/summary")["open_positions"] == 0


# --- rebuild criterion (§15) ------------------------------------------------------------


def _engine_tx(row: dict) -> Transaction:
    def opt(name):
        return None if row[name] is None else Decimal(row[name])

    return Transaction(
        id=UUID(row["id"]), type=TransactionType(row["type"]),
        trade_date=date.fromisoformat(row["trade_date"]),
        sequence=row["sequence"], asset_id=row["asset_id"] and UUID(row["asset_id"]),
        quantity=opt("quantity"), unit_price=opt("unit_price"),
        gross_amount=opt("gross_amount"), fees=opt("fees"),
        accrued_interest_paid=opt("accrued_interest_paid"),
        accrued_interest_received=opt("accrued_interest_received"), taxes=opt("taxes"),
        total_amount=opt("total_amount"), affects_cash=row["affects_cash"],
    )  # fmt: skip


def test_figures_equal_a_fresh_replay_of_the_stored_transactions(
    client: TestClient, account
) -> None:
    etf = e3(client, account)
    stored = get(client, "/api/v1/transactions", account_id=account["id"])["items"]

    ledger = replay([_engine_tx(r) for r in stored])
    asset = Asset(UUID(etf["id"]), AssetType.ETF, "EUR", QuoteUnit.CURRENCY)
    history = PriceHistory({date(2025, 12, 30): Decimal("110.00")})
    expected = value_account(
        ledger, {asset.id: asset}, {asset.id: history}, FxRates({}), "EUR", date(2026, 1, 15)
    )

    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert dec(summary, "total_value", "cash", "unrealized", "gain_gross", "gain_net") == (
        expected.total_value, expected.cash, expected.unrealized, expected.gain_gross,
        expected.gain_net,
    )  # fmt: skip
    assert Decimal(summary["realized_net"]) == expected.totals.realized_net
    assert Decimal(summary["income_net"]) == expected.totals.income_net


# --- isolation --------------------------------------------------------------------------


def test_another_user_sees_nothing_of_my_accounts(client: TestClient, account) -> None:
    e3(client, account)
    bob = investor(client, "bob")
    for path in ("summary", "positions"):
        response = client.get(f"/api/v1/accounts/{account['id']}/{path}", headers=bob)
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert get(client, "/api/v1/portfolio/summary", bob)["account_count"] == 0


def test_my_manual_price_never_values_another_users_position(client: TestClient, account) -> None:
    """A shared asset (provider-resolved in M4, inserted directly here) held by two users:
    each user's manual price values only their own position (D-35)."""
    with Session(client.app.state.engine) as db, db.begin():
        shared = AssetRow(isin=fake_isin(7), name="Shared ETF", **{k: ETF[k] for k in (
            "asset_type", "quote_currency", "quote_unit")})  # fmt: skip
        db.add(shared)
        db.flush()
        shared_id = str(shared.id)
    bob = investor(client, "bob")
    bobs = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, bob)
    for headers, acc in ((CSRF, account), (bob, bobs)):
        tx(client, acc, "BUY", "2025-01-10", headers=headers, asset_id=shared_id, quantity="1",
           unit_price="100.00", gross_amount="100.00", total_amount="100.00",
           affects_cash=False)  # fmt: skip
    price(client, {"id": shared_id}, "2025-12-30", "150.00")

    [mine] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    [his] = get(client, f"/api/v1/accounts/{bobs['id']}/positions", bob)
    assert Decimal(mine["value"]) == 150 and mine["estimated_at_cost"] is False
    assert Decimal(his["value"]) == 100 and his["estimated_at_cost"] is True

    price(client, {"id": shared_id}, "2025-12-31", "90.00", headers=bob)
    [mine] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert mine["price_date"] == "2025-12-30" and Decimal(mine["value"]) == 150


@pytest.mark.parametrize(
    "path", ["/api/v1/portfolio/summary", "/api/v1/accounts/{id}/summary",
             "/api/v1/accounts/{id}/positions"],
)  # fmt: skip
def test_reads_need_a_session(client: TestClient, account, path) -> None:
    client.cookies.clear()
    response = client.get(path.format(id=account["id"]))
    assert response.status_code == 401
