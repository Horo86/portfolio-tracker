"""Derived reads with stored FX rates (domain-rules §9, §12; D-64, D-73, D-120; E11 and E12
through the API): conversions through EUR rounded once, the account's value in the reporting
currency, the stale price and stale FX notices, partial totals for a missing rate. The test
clock says 2026-01-15 (UTC). Invented data only."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin
from test_api_reports import US_STOCK, dec, get, post, price, tx

from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata.base import Rate
from portfolio_tracker.repositories import fx

GBX_STOCK = {"name": "Example London Plc", "asset_type": "STOCK", "quote_currency": "GBP",
             "quote_unit": "MINOR_CURRENCY_UNIT"}  # fmt: skip
EUR_ETF = {"name": "Example ETF", "asset_type": "ETF", "quote_currency": "EUR",
           "quote_unit": "CURRENCY"}  # fmt: skip


def rates(client: TestClient, *rows: tuple[str, str, str]) -> None:
    """(day, currency, units per EUR) stored as provider rates."""
    with db_transaction(client.app.state.engine, write=True) as session:
        fx.store_rates(
            session,
            [Rate(date.fromisoformat(d), c, Decimal(r)) for d, c, r in rows],
            "frankfurter",
        )


def summary(client, account) -> dict:
    return get(client, f"/api/v1/accounts/{account['id']}/summary")


def positions(client, account) -> list[dict]:
    return get(client, f"/api/v1/accounts/{account['id']}/positions")


@pytest.fixture
def admin(client: TestClient) -> None:
    create_admin(client)


def usd_account_of_e12(client) -> dict:
    """E12's USD account: cost basis 1000.00, securities 1200.00, cash 100.00, realized
    +50.00 (BUY 15 @ 100.00, SELL 5 @ 110.00, 10 left at 120.00)."""
    usd = post(client, "/api/v1/accounts", {"name": "US", "currency": "USD"})
    stock = post(client, "/api/v1/assets", US_STOCK | {"isin": fake_isin(21)})
    tx(client, usd, "DEPOSIT", "2025-01-02", total_amount="1050.00")
    tx(client, usd, "BUY", "2025-01-10", asset_id=stock["id"], quantity="15",
       unit_price="100.00", gross_amount="1500.00", total_amount="1500.00")  # fmt: skip
    tx(client, usd, "SELL", "2025-03-10", asset_id=stock["id"], quantity="5",
       unit_price="110.00", gross_amount="550.00", total_amount="550.00")  # fmt: skip
    price(client, stock, "2026-01-14", "120.00")
    return usd


# --- worked examples ----------------------------------------------------------------------------


def test_example_e12_reporting_currency_with_a_usd_account(client: TestClient, admin) -> None:
    usd = usd_account_of_e12(client)
    eur = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    tx(client, eur, "DEPOSIT", "2025-01-02", total_amount="2000.00")
    rates(client, ("2026-01-14", "USD", "1.25"))

    account = summary(client, usd)
    assert dec(account, "invested_capital", "securities_value", "cash", "realized_gross") == (
        Decimal(1000), Decimal(1200), Decimal(100), Decimal(50),
    )  # fmt: skip
    assert account["reporting"] == {"currency": "EUR", "total_value": "1040.00000000",
                                    "stale_fx": None}  # fmt: skip

    portfolio = get(client, "/api/v1/portfolio/summary")
    assert portfolio["partial"] is False and portfolio["unavailable_currencies"] == []
    assert dec(portfolio, "invested_capital", "securities_value", "unrealized") == (
        Decimal(800), Decimal(960), Decimal(160),
    )  # fmt: skip
    assert dec(portfolio, "cash", "realized_gross", "total_value") == (
        Decimal(2080), Decimal(40), Decimal(3040),
    )  # fmt: skip
    assert portfolio["stale_fx"] == []
    assert all(isinstance(portfolio[k], str) for k in ("total_value", "cash", "unrealized"))


def test_example_e11_pence_converted_to_euros(client: TestClient, admin) -> None:
    account = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    stock = post(client, "/api/v1/assets", GBX_STOCK | {"isin": fake_isin(22)})
    tx(client, account, "BUY", "2025-06-02", asset_id=stock["id"], quantity="100",
       unit_price="1200", gross_amount="1500.00", fees="5.00", total_amount="1505.00",
       affects_cash=False)  # fmt: skip
    price(client, stock, "2026-01-14", "1250")
    rates(client, ("2026-01-14", "GBP", "0.80"))

    [p] = positions(client, account)
    assert dec(p, "cost_basis", "value", "unrealized") == (
        Decimal(1505), Decimal("1562.50"), Decimal("57.50"),
    )  # fmt: skip
    assert Decimal(p["unrealized_pct"]) == Decimal("0.03820598")  # 57.50 / 1505.00
    assert p["value_unavailable"] is False and p["fx_stale"] is None
    assert summary(client, account)["partial"] is False


def test_a_cross_rate_goes_through_eur_and_is_rounded_once(client: TestClient, admin) -> None:
    """USD → GBP = amount x GBP per EUR / USD per EUR, one rounding to 8 decimals (D-71).
    Rounding the EUR leg first would give 0.72649572 (1 / 1.17 = 0.85470085, x 0.85)."""
    gbp = post(client, "/api/v1/accounts", {"name": "UK", "currency": "GBP"})
    stock = post(client, "/api/v1/assets", US_STOCK | {"isin": fake_isin(23)})
    tx(client, gbp, "BUY", "2025-06-02", asset_id=stock["id"], quantity="1",
       unit_price="1.00", gross_amount="0.70", total_amount="0.70",
       affects_cash=False)  # fmt: skip
    price(client, stock, "2026-01-14", "1.00")
    rates(client, ("2026-01-14", "USD", "1.17"), ("2026-01-14", "GBP", "0.85"))

    [p] = positions(client, gbp)
    assert p["value"] == "0.72649573"  # 1 x 0.85 / 1.17 = 0.726495726..., rounded once


# --- notices --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("day", "stale"), [("2026-01-10", False), ("2026-01-09", True)])
def test_a_price_older_than_5_days_is_stale(client: TestClient, admin, day, stale) -> None:
    account = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    etf = post(client, "/api/v1/assets", EUR_ETF | {"isin": fake_isin(24)})
    tx(client, account, "BUY", "2025-06-02", asset_id=etf["id"], quantity="10",
       unit_price="10.00", gross_amount="100.00", total_amount="100.00",
       affects_cash=False)  # fmt: skip
    price(client, etf, day, "11.00")

    [p] = positions(client, account)
    assert (p["price_date"], p["price_stale"]) == (day, stale)
    assert Decimal(p["value"]) == Decimal(110)  # a stale price stays in the totals (§12)


def test_a_stale_rate_is_named_with_its_pair_and_date(client: TestClient, admin) -> None:
    eur = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    stock = post(client, "/api/v1/assets", US_STOCK | {"isin": fake_isin(25)})
    tx(client, eur, "BUY", "2025-06-02", asset_id=stock["id"], quantity="2",
       unit_price="50.00", gross_amount="90.00", total_amount="90.00",
       affects_cash=False)  # fmt: skip
    price(client, stock, "2026-01-14", "60.00")
    usd = usd_account_of_e12(client)
    rates(client, ("2026-01-09", "USD", "1.25"))  # 6 days before the valuation day
    notice = {"from_currency": "USD", "to_currency": "EUR", "rate_date": "2026-01-09"}

    [p] = positions(client, eur)
    assert p["fx_stale"] == notice and Decimal(p["value"]) == Decimal("96.00000000")
    assert summary(client, eur)["stale_fx"] == [notice]  # still used: not partial
    assert summary(client, eur)["partial"] is False
    assert summary(client, usd)["reporting"]["stale_fx"] == notice
    assert summary(client, usd)["stale_fx"] == []  # its own figures need no rate
    portfolio = get(client, "/api/v1/portfolio/summary")
    assert portfolio["stale_fx"] == [notice] and portfolio["partial"] is False

    rates(client, ("2026-01-10", "USD", "1.25"))  # 5 days: not stale
    assert get(client, "/api/v1/portfolio/summary")["stale_fx"] == []


def test_a_cross_conversion_names_the_older_of_its_two_rates(client: TestClient, admin) -> None:
    gbp = post(client, "/api/v1/accounts", {"name": "UK", "currency": "GBP"})
    stock = post(client, "/api/v1/assets", US_STOCK | {"isin": fake_isin(26)})
    tx(client, gbp, "BUY", "2025-06-02", asset_id=stock["id"], quantity="1",
       unit_price="100.00", gross_amount="70.00", total_amount="70.00",
       affects_cash=False)  # fmt: skip
    price(client, stock, "2026-01-14", "100.00")
    rates(client, ("2026-01-14", "USD", "1.17"), ("2026-01-02", "GBP", "0.85"))

    [p] = positions(client, gbp)
    assert p["fx_stale"] == {"from_currency": "USD", "to_currency": "GBP",
                             "rate_date": "2026-01-02"}  # fmt: skip


def test_a_missing_rate_makes_the_totals_partial(client: TestClient, admin) -> None:
    usd = usd_account_of_e12(client)
    eur = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    tx(client, eur, "DEPOSIT", "2025-01-02", total_amount="2000.00")
    rates(client, ("2026-01-14", "GBP", "0.85"))  # no USD rate

    assert summary(client, usd)["reporting"] == {"currency": "EUR", "total_value": None,
                                                 "stale_fx": None}  # fmt: skip
    portfolio = get(client, "/api/v1/portfolio/summary")
    assert portfolio["partial"] is True and portfolio["unavailable_currencies"] == ["USD"]
    assert Decimal(portfolio["total_value"]) == Decimal(2000)  # the USD account is left out


def test_the_reporting_value_follows_the_users_currency(client: TestClient, admin) -> None:
    eur = post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    tx(client, eur, "DEPOSIT", "2025-01-02", total_amount="2000.00")
    assert summary(client, eur)["reporting"]["total_value"] == "2000.00"  # no conversion
    response = client.patch("/api/v1/me", headers=CSRF, json={"reporting_currency": "USD"})
    assert response.status_code == 200, response.text
    rates(client, ("2026-01-14", "USD", "1.25"))
    reporting = summary(client, eur)["reporting"]
    assert (reporting["currency"], Decimal(reporting["total_value"])) == ("USD", Decimal(2500))


def test_an_account_with_no_transactions_gives_no_stale_rate_notice(client, admin) -> None:
    """D-153: an empty account does not count, so neither does its stale rate."""
    rates(client, ("2026-01-02", "CHF", "0.93"))  # 13 days old on the test clock: stale
    eur = post(client, "/api/v1/accounts", {"name": "EUR", "currency": "EUR"})
    tx(client, eur, "DEPOSIT", "2025-06-02", total_amount="100.00")
    post(client, "/api/v1/accounts", {"name": "CHF", "currency": "CHF"})

    portfolio = get(client, "/api/v1/portfolio/summary")
    assert portfolio["partial"] is False and portfolio["stale_fx"] == []
