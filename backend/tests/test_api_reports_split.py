"""A manual price older than a split recorded in the account (domain-rules §8, D-202), through
the API: every figure reads the converted price, and only that case changes. The test clock says
2026-01-15 (UTC). Invented data only."""

import dataclasses
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from helpers import create_admin
from test_api_reports import ETF, _engine_tx, dec, get, post, price, tx

from portfolio_tracker.engine.ledger import replay
from portfolio_tracker.engine.model import Asset, AssetType, QuoteUnit, SplitRatio, Transaction
from portfolio_tracker.engine.valuation import FxRates, PriceHistory, value_account

DAY = date(2026, 1, 15)
FIGURES = ("total_value", "securities_value", "cash", "invested_capital", "unrealized",
           "gain_gross", "gain_net")  # fmt: skip
ROW = ("quantity", "price", "value", "unrealized", "gain_gross")


@pytest.fixture
def account(client: TestClient) -> dict:
    create_admin(client)
    return post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})


def holding(client, account, name: str, *, split_on: str | None = None) -> dict:
    """10 shares for 1000.00 on 2 March 2025, then a 2:1 split if asked."""
    asset = post(client, "/api/v1/assets", ETF | {"name": name})
    tx(client, account, "BUY", "2025-03-02", asset_id=asset["id"], quantity="10",
       unit_price="100.00", gross_amount="1000.00", total_amount="1000.00",
       affects_cash=False)  # fmt: skip
    if split_on:
        tx(client, account, "STOCK_SPLIT", split_on, asset_id=asset["id"], split_ratio="2:1")
    return asset


def unaffected(client, account) -> dict[str, dict]:
    """Every case without a manual price older than a later split."""
    after = holding(client, account, "After the split", split_on="2025-04-15")
    price(client, after, "2025-05-02", "55.00")
    same_day = holding(client, account, "Priced on the split day", split_on="2025-04-15")
    price(client, same_day, "2025-04-15", "51.00")
    no_split = holding(client, account, "No split")
    price(client, no_split, "2025-03-31", "110.00")
    at_cost = holding(client, account, "Split without a price", split_on="2025-04-15")
    return {"after": after, "same_day": same_day, "no_split": no_split, "at_cost": at_cost}


def affected(client, account) -> dict:
    """E14 dated in 2025: 120.00 entered on 31 March, then the 2:1 split on 15 April."""
    asset = holding(client, account, "Priced before the split", split_on="2025-04-15")
    price(client, asset, "2025-03-31", "120.00")
    return asset


def engine_tx(row: dict) -> Transaction:
    """`_engine_tx` with the split ratio, stored as `new:old`."""
    if row["split_ratio"] is None:
        return _engine_tx(row)
    new, old = row["split_ratio"].split(":")
    return dataclasses.replace(_engine_tx(row), split_ratio=SplitRatio(Decimal(new), Decimal(old)))


def old_rule(client, account, prices: dict[str, tuple[str, str]]):
    """A fresh replay valued with every price as entered: the figures before D-202."""
    stored = get(client, "/api/v1/transactions", account_id=account["id"])["items"]
    ledger = replay([engine_tx(r) for r in stored])
    assets = {i: Asset(i, AssetType.ETF, "EUR", QuoteUnit.CURRENCY) for i in ledger.positions}
    histories = {
        UUID(i): PriceHistory({date.fromisoformat(on): Decimal(close)})
        for i, (on, close) in prices.items()
    }
    return value_account(ledger, assets, histories, FxRates({}), "EUR", DAY)


def summary_of(client, account) -> tuple:
    return dec(get(client, f"/api/v1/accounts/{account['id']}/summary"), *FIGURES)


def rows_of(client, account) -> dict[str, tuple]:
    positions = get(client, f"/api/v1/accounts/{account['id']}/positions")
    return {p["asset_id"]: (*dec(p, *ROW), p["price_date"], p["price_stale"]) for p in positions}


def test_summary_and_holdings_agree_on_the_converted_price(client: TestClient, account) -> None:
    asset = affected(client, account)

    [position] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert dec(position, "quantity", "price", "value", "unrealized") == (
        Decimal(20), Decimal(60), Decimal(1200), Decimal(200),
    )  # fmt: skip
    assert (position["price_source"], position["price_date"]) == ("MANUAL", "2025-03-31")
    assert position["price_stale"] is True  # dated 31 March, as entered: unchanged (§12)
    assert position["estimated_at_cost"] is False

    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert dec(summary, "securities_value", "unrealized") == (Decimal(1200), Decimal(200))
    portfolio = get(client, "/api/v1/portfolio/summary")
    assert dec(portfolio, "securities_value", "unrealized") == (Decimal(1200), Decimal(200))

    [row] = get(client, "/api/v1/portfolio/positions")["positions"]
    assert row["asset_id"] == asset["id"]
    assert dec(row, "quantity", "price", "value") == (Decimal(20), Decimal(60), Decimal(1200))
    assert (row["price"], row["price_date"], row["price_stale"]) == (
        position["price"], "2025-03-31", True,
    )  # fmt: skip
    # Holding and price list still show the price as entered.
    entered = get(client, f"/api/v1/assets/{asset['id']}/prices")
    assert [(p["day"], p["close"]) for p in entered] == [("2025-03-31", "120.00")]


def test_only_a_manual_price_older_than_a_split_changes_the_figures(
    client: TestClient, account
) -> None:
    cases = unaffected(client, account)
    prices = {
        cases["after"]["id"]: ("2025-05-02", "55.00"),
        cases["same_day"]["id"]: ("2025-04-15", "51.00"),
        cases["no_split"]["id"]: ("2025-03-31", "110.00"),
    }
    before = old_rule(client, account, prices)
    assert summary_of(client, account) == (
        before.total_value, before.securities_value, before.cash,
        before.totals.invested_capital, before.unrealized, before.gain_gross, before.gain_net,
    )  # fmt: skip
    assert dec(get(client, f"/api/v1/accounts/{account['id']}/summary"), "securities_value") == (
        Decimal(1100 + 1020 + 1100 + 1000),  # 20 x 55, 20 x 51, 10 x 110, at cost
    )
    rows = rows_of(client, account)
    for asset_id, value in before.positions.items():
        assert rows[str(asset_id)][2] == value.value

    asset = affected(client, account)
    now = summary_of(client, account)
    assert rows_of(client, account) == rows | {
        asset["id"]: (Decimal(20), Decimal(60), Decimal(1200), Decimal(200), Decimal(200),
                      "2025-03-31", True),
    }  # fmt: skip
    old = old_rule(client, account, prices | {asset["id"]: ("2025-03-31", "120.00")})
    assert old.positions[UUID(asset["id"])].value == Decimal(2400)  # the bug: 20 x 120.00
    # Only that position moves: the totals differ from the old rule by 2400 - 1200.
    assert now == tuple(
        v - Decimal(1200) if name in ("total_value", "securities_value", "unrealized",
                                      "gain_gross", "gain_net") else v
        for name, v in zip(FIGURES, (
            old.total_value, old.securities_value, old.cash, old.totals.invested_capital,
            old.unrealized, old.gain_gross, old.gain_net,
        ), strict=True)
    )  # fmt: skip


def test_each_account_counts_its_own_splits(client: TestClient, account) -> None:
    asset = affected(client, account)
    other = post(client, "/api/v1/accounts", {"name": "Altro", "currency": "EUR"})
    # Bought after the split, in the new units, with no split in this account: the manual
    # price is used as entered there (§8, D-202: enter a new manual price).
    tx(client, other, "BUY", "2025-05-02", asset_id=asset["id"], quantity="10",
       unit_price="60.00", gross_amount="600.00", total_amount="600.00",
       affects_cash=False)  # fmt: skip

    [here] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    [there] = get(client, f"/api/v1/accounts/{other['id']}/positions")
    assert dec(here, "quantity", "price", "value") == (Decimal(20), Decimal(60), Decimal(1200))
    assert dec(there, "quantity", "price", "value") == (Decimal(10), Decimal(120), Decimal(1200))
    [row] = get(client, "/api/v1/portfolio/positions")["positions"]
    assert dec(row, "quantity", "value") == (Decimal(30), Decimal(2400))
