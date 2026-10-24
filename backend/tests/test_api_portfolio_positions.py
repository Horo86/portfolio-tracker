"""`GET /portfolio/positions`: the holdings of every account in the reporting currency (D-143).
The test clock says 2026-01-15 (UTC). Invented data only."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import create_admin, fake_isin, investor
from test_api_reports import BOND, ETF, US_STOCK, get, post, price, tx
from test_api_reports_fx import rates

PATH = "/api/v1/portfolio/positions"


@pytest.fixture
def accounts(client: TestClient) -> tuple[dict, dict]:
    create_admin(client)
    eur = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    usd = post(client, "/api/v1/accounts", {"name": "US", "currency": "USD"})
    return eur, usd


def holdings(client, accounts) -> tuple[dict, dict]:
    """An ETF in the euro account; a US stock in both (cost 800 EUR + 450 USD)."""
    eur, usd = accounts
    etf = post(client, "/api/v1/assets", ETF | {"isin": fake_isin(51)})
    stock = post(client, "/api/v1/assets", US_STOCK)
    tx(client, eur, "BUY", "2025-03-03", asset_id=etf["id"], quantity="10", unit_price="100.00",
       gross_amount="1000.00", total_amount="1000.00", affects_cash=False)  # fmt: skip
    tx(client, eur, "BUY", "2025-03-03", asset_id=stock["id"], quantity="10",
       unit_price="100.00", gross_amount="800.00", total_amount="800.00",
       affects_cash=False)  # fmt: skip
    tx(client, usd, "BUY", "2025-03-04", asset_id=stock["id"], quantity="5", unit_price="90.00",
       gross_amount="450.00", total_amount="450.00", affects_cash=False)  # fmt: skip
    price(client, etf, "2026-01-14", "110.00")
    price(client, stock, "2026-01-14", "100.00")
    return etf, stock


def rows(body: dict) -> dict[str, dict]:
    return {p["asset_id"]: p for p in body["positions"]}


def test_one_row_per_asset_over_all_accounts(client: TestClient, accounts) -> None:
    eur, usd = accounts
    etf, stock = holdings(client, accounts)
    rates(client, ("2026-01-14", "USD", "1.25"))

    body = get(client, PATH)
    assert (body["currency"], body["day"]) == ("EUR", "2026-01-15")
    assert (body["partial"], body["unavailable_currencies"], body["stale_fx"]) == (False, [], [])
    assert [p["name"] for p in body["positions"]] == ["Example ETF", "Example US Corp"]
    us = rows(body)[stock["id"]]
    assert set(us["account_ids"]) == {eur["id"], usd["id"]}
    assert Decimal(us["quantity"]) == 15
    assert Decimal(us["cost_basis"]) == 1160  # 800 + 450 / 1.25
    assert us["pmc"] == "77.33333333"  # in EUR (§9), not in the quote currency
    assert (Decimal(us["value"]), Decimal(us["unrealized"])) == (1200, 40)
    assert us["unrealized_pct"] == "0.03448276"
    assert (us["price"], us["price_date"], us["price_stale"]) == ("100.00", "2026-01-14", False)
    assert (us["price_source"], us["price_status"]) == ("MANUAL", "MANUAL")
    assert (us["partial"], us["value_unavailable"], us["estimated_at_cost"]) == (False,) * 3
    assert us["stale_fx"] == []
    euro = rows(body)[etf["id"]]
    assert euro["account_ids"] == [eur["id"]]
    assert (euro["price_status"], euro["isin"], euro["open"]) == ("PENDING", fake_isin(51), True)
    assert Decimal(euro["gain_gross"]) == 100


def test_closed_positions_only_when_asked(client: TestClient, accounts) -> None:
    eur, _ = accounts
    etf, _ = holdings(client, accounts)
    tx(client, eur, "SELL", "2025-06-02", asset_id=etf["id"], quantity="10", unit_price="105.00",
       gross_amount="1050.00", total_amount="1050.00")  # fmt: skip
    rates(client, ("2026-01-14", "USD", "1.25"))

    assert etf["id"] not in rows(get(client, PATH))
    closed = rows(get(client, PATH, include_closed="true"))[etf["id"]]
    assert (closed["open"], Decimal(closed["quantity"]), closed["pmc"]) == (False, 0, None)
    assert (Decimal(closed["realized_gross"]), Decimal(closed["realized_pct_gross"])) == (
        50,
        Decimal("0.05"),
    )


def test_an_account_without_a_rate_is_left_out(client: TestClient, accounts) -> None:
    eur, _ = accounts
    _, stock = holdings(client, accounts)  # no rate stored at all

    body = get(client, PATH)
    assert (body["partial"], body["unavailable_currencies"]) == (True, ["USD"])
    us = rows(body)[stock["id"]]
    assert us["account_ids"] == [eur["id"]]  # the dollar account's 5 are not in it
    assert (Decimal(us["quantity"]), Decimal(us["cost_basis"])) == (10, 800)
    assert (us["value_unavailable"], us["partial"]) == (True, True)
    assert [us[k] for k in ("value", "unrealized", "gain_gross", "gain_net")] == [None] * 4
    assert us["price"] == "100.00"  # the close is known, only the rate is missing


def test_a_stale_rate_is_named_once_on_the_row_and_on_the_response(
    client: TestClient, accounts
) -> None:
    _, stock = holdings(client, accounts)
    rates(client, ("2026-01-05", "USD", "1.25"))  # 10 days old on the valuation day

    body = get(client, PATH)
    notice = {"from_currency": "USD", "to_currency": "EUR", "rate_date": "2026-01-05"}
    assert rows(body)[stock["id"]]["stale_fx"] == [notice]
    assert body["stale_fx"] == [notice]
    assert body["partial"] is False


def test_a_bond_average_cost_is_a_percentage_of_nominal(client: TestClient, accounts) -> None:
    eur, _ = accounts
    bond = post(client, "/api/v1/assets", BOND)
    tx(client, eur, "BUY", "2025-03-03", asset_id=bond["id"], quantity="1000",
       unit_price="97.60", gross_amount="976.00", total_amount="976.00",
       affects_cash=False)  # fmt: skip

    [row] = get(client, PATH)["positions"]
    assert Decimal(row["pmc"]) == Decimal("97.6")  # D-66


def test_another_user_sees_none_of_my_holdings(client: TestClient, accounts) -> None:
    holdings(client, accounts)
    bob = investor(client, "bob")
    body = get(client, PATH, bob)
    assert body["positions"] == [] and body["partial"] is False
    assert len(get(client, PATH)["positions"]) == 2  # the owner's read is unchanged


def test_notices_count_only_the_rows_returned(client: TestClient, accounts) -> None:
    eur, usd = accounts
    etf = post(client, "/api/v1/assets", ETF)
    stock = post(client, "/api/v1/assets", US_STOCK)
    tx(client, eur, "BUY", "2025-03-03", asset_id=etf["id"], quantity="10", unit_price="100.00",
       gross_amount="1000.00", total_amount="1000.00", affects_cash=False)  # fmt: skip
    tx(client, usd, "BUY", "2025-03-04", asset_id=stock["id"], quantity="5", unit_price="90.00",
       gross_amount="450.00", total_amount="450.00", affects_cash=False)  # fmt: skip
    tx(client, usd, "SELL", "2025-06-02", asset_id=stock["id"], quantity="5",
       unit_price="95.00", gross_amount="475.00", total_amount="475.00")  # fmt: skip
    rates(client, ("2026-01-05", "USD", "1.25"))  # stale: used only by the closed position

    shown = get(client, PATH)
    assert [p["asset_id"] for p in shown["positions"]] == [etf["id"]]
    assert (shown["stale_fx"], shown["partial"]) == ([], False)
    everything = get(client, PATH, include_closed="true")
    notice = {"from_currency": "USD", "to_currency": "EUR", "rate_date": "2026-01-05"}
    assert everything["stale_fx"] == [notice]
