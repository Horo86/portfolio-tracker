"""Breakdown of a view: allocation and income history (D-153, domain-rules §13; E13 through
the API). The test clock says 2026-01-15 (UTC). Invented data only."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, investor
from test_api_reports import get, post, price, tx
from test_api_reports_fx import EUR_ETF, rates

BOND_USD = {"name": "Example Treasury 2031", "asset_type": "BOND", "quote_currency": "USD",
            "quote_unit": "PERCENT_OF_NOMINAL"}  # fmt: skip
STOCK_EUR = {"name": "Example Industrials SpA", "asset_type": "STOCK", "quote_currency": "EUR",
             "quote_unit": "CURRENCY"}  # fmt: skip


def shares(items: list[dict], key: str = "key") -> list[tuple]:
    return [(i[key], Decimal(i["value"]), i["share"] and Decimal(i["share"])) for i in items]


def amounts(item: dict) -> tuple:
    return tuple(Decimal(item[k]) for k in ("gross", "taxes", "net"))


@pytest.fixture
def e13(client: TestClient) -> dict:
    """E13's four accounts, priced on 14 Jan 2026 with EUR/USD 1.25 and no CHF or GBP rate."""
    create_admin(client)
    rates(client, ("2026-01-14", "USD", "1.25"))
    a = post(client, "/api/v1/accounts", {"name": "A", "currency": "EUR"})
    x = post(client, "/api/v1/assets", EUR_ETF)
    y = post(client, "/api/v1/assets", STOCK_EUR)
    tx(client, a, "DEPOSIT", "2025-06-02", total_amount="885.20")
    tx(client, a, "BUY", "2025-06-02", asset_id=x["id"], quantity="5", unit_price="100",
       gross_amount="500.00", total_amount="500.00")  # fmt: skip
    tx(client, a, "BUY", "2025-06-02", asset_id=y["id"], quantity="3", unit_price="100",
       gross_amount="300.00", total_amount="300.00")  # fmt: skip
    tx(client, a, "DIVIDEND", "2025-10-10", asset_id=y["id"], gross_amount="20.00",
       taxes="5.20", total_amount="14.80")  # fmt: skip
    price(client, x, "2026-01-14", "120")
    b = post(client, "/api/v1/accounts", {"name": "B", "currency": "USD"})
    z = post(client, "/api/v1/assets", BOND_USD)
    tx(client, b, "DEPOSIT", "2025-06-02", total_amount="418.13")
    tx(client, b, "BUY", "2025-06-02", asset_id=z["id"], quantity="500", unit_price="98",
       gross_amount="490.00", total_amount="490.00")  # fmt: skip
    tx(client, b, "COUPON", "2025-12-01", asset_id=z["id"], gross_amount="25.00",
       taxes="3.13", total_amount="21.87")  # fmt: skip
    price(client, z, "2026-01-14", "100")
    post(client, "/api/v1/accounts", {"name": "C", "currency": "CHF"})
    d = post(client, "/api/v1/accounts", {"name": "D", "currency": "GBP"})
    tx(client, d, "DEPOSIT", "2025-06-02", total_amount="10.00")
    return {"a": a, "b": b, "x": x, "y": y, "z": z}


def test_portfolio_breakdown_of_e13(client: TestClient, e13: dict) -> None:
    body = get(client, "/api/v1/portfolio/breakdown")
    assert (body["currency"], body["day"]) == ("EUR", "2026-01-15")
    assert body["partial"] is True and body["unavailable_currencies"] == ["GBP"]
    assert shares(body["by_type"]) == [
        ("ETF", Decimal("600.00"), Decimal("0.42857143")),
        ("BOND", Decimal("400.00"), Decimal("0.28571429")),
        ("STOCK", Decimal("300.00"), Decimal("0.21428571")),
        ("CASH", Decimal("100.00"), Decimal("0.07142857")),
    ]
    assert shares(body["by_currency"]) == [
        ("EUR", Decimal("1000.00"), Decimal("0.71428571")),
        ("USD", Decimal("400.00"), Decimal("0.28571429")),
    ]
    assert [(i["asset_id"], i["name"]) for i in body["by_asset"]] == [
        (e13["x"]["id"], "Example ETF"),
        (e13["z"]["id"], "Example Treasury 2031"),
        (e13["y"]["id"], "Example Industrials SpA"),
    ]
    assert Decimal(body["negative_cash"]) == Decimal("-40.00")
    months = body["income_by_month"]
    assert [m["month"] for m in months][:1] + [m["month"] for m in months][-1:] == [
        "2025-02", "2026-01"]  # fmt: skip
    by_month = {m["month"]: amounts(m) for m in months}
    assert by_month["2025-10"] == (Decimal("20.00"), Decimal("5.20"), Decimal("14.80"))
    assert by_month["2025-12"] == (Decimal("20.00"), Decimal("2.504"), Decimal("17.496"))
    assert sum(1 for m in months if Decimal(m["gross"]) == 0) == 10
    assert [(y["year"], amounts(y)) for y in body["income_by_year"]] == [
        (2025, (Decimal("40.00"), Decimal("7.704"), Decimal("32.296"))),
        (2026, (Decimal("0"), Decimal("0"), Decimal("0"))),
    ]
    assert amounts(body["income_total"]) == (Decimal("40.00"), Decimal("7.704"), Decimal("32.296"))


def test_account_breakdown_is_in_the_account_currency(client: TestClient, e13: dict) -> None:
    body = get(client, f"/api/v1/accounts/{e13['b']['id']}/breakdown")
    assert body["currency"] == "USD" and body["partial"] is False
    assert body["unavailable_currencies"] == []
    assert shares(body["by_type"]) == [("BOND", Decimal("500.00"), Decimal("1"))]
    assert shares(body["by_currency"]) == [("USD", Decimal("500.00"), Decimal("1"))]
    assert Decimal(body["negative_cash"]) == Decimal("-50.00")
    by_month = {m["month"]: amounts(m) for m in body["income_by_month"]}
    assert by_month["2025-12"] == (Decimal("25.00"), Decimal("3.13"), Decimal("21.87"))


def test_an_empty_portfolio_has_nothing_to_break_down(client: TestClient) -> None:
    create_admin(client)
    body = get(client, "/api/v1/portfolio/breakdown")
    assert (body["by_type"], body["by_currency"], body["by_asset"]) == ([], [], [])
    assert len(body["income_by_month"]) == 12 and body["income_by_year"] == []
    assert body["partial"] is False and Decimal(body["negative_cash"]) == 0


def test_another_users_account_is_404_and_never_in_the_portfolio(client: TestClient) -> None:
    create_admin(client)
    other = investor(client, "bob")
    theirs = post(client, "/api/v1/accounts", {"name": "Bob's", "currency": "EUR"}, headers=other)
    tx(client, theirs, "DEPOSIT", "2025-06-02", headers=other, total_amount="999.00")

    response = client.get(f"/api/v1/accounts/{theirs['id']}/breakdown")  # the Administrator
    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert get(client, "/api/v1/portfolio/breakdown")["by_type"] == []
    own = get(client, "/api/v1/portfolio/breakdown", headers=other)
    assert shares(own["by_type"]) == [("CASH", Decimal("999.00"), Decimal("1"))]


def test_breakdown_needs_a_session(client: TestClient) -> None:
    create_admin(client)
    client.post("/api/v1/auth/logout", headers=CSRF)
    assert client.get("/api/v1/portfolio/breakdown").status_code == 401
