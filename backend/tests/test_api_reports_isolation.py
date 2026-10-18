"""E3 and E4 through the API with provider closes, and isolation (D-116): two users hold the
same ISIN, so their assets share one instrument; nothing in one user's responses shows an
instrument, a listing or anything of the other user, and nothing changes when the other user
appears. Provider data is stored directly. Invented data only."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import create_admin, fake_isin, investor
from sqlalchemy import select
from test_api_reports import BOND, ETF, dec, e3, get, post, price, tx
from test_api_reports_status import provider

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.marketdata.base import PriceClass
from portfolio_tracker.services.market_data import link_assets


@pytest.fixture
def account(client: TestClient) -> dict:
    create_admin(client)
    return post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})


def test_example_e3_with_a_provider_close(client: TestClient, account) -> None:
    etf = e3(client, account)  # its manual price of 30 December, older than the provider's
    provider(client, etf["isin"], {"2026-01-14": "110.00"})

    [p] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert (p["price_date"], p["price_source"], p["price_status"]) == (
        "2026-01-14",
        "PROVIDER",
        "OK",
    )
    summary = get(client, f"/api/v1/accounts/{account['id']}/summary")
    assert dec(summary, "unrealized", "unrealized_pct") == (Decimal(60), Decimal("0.1"))
    assert dec(summary, "gain_gross", "taxes", "gain_net") == (
        Decimal(160), Decimal(26), Decimal(134),
    )  # fmt: skip


def test_example_e4_with_a_bond_close_in_percent_of_nominal(client: TestClient, account) -> None:
    bond = post(client, "/api/v1/assets", BOND | {"isin": fake_isin(41)})
    tx(client, account, "BUY", "2025-02-03", asset_id=bond["id"], quantity="5000",
       unit_price="97.50", gross_amount="4875.00", fees="5.00", accrued_interest_paid="12.34",
       taxes="-2.44", total_amount="4889.90")  # fmt: skip
    provider(
        client,
        bond["isin"],
        {"2026-01-14": "98.20"},
        unit="PERCENT_OF_NOMINAL",
        price_class=PriceClass.BOND,
    )

    [p] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    assert (p["price_source"], p["price_status"]) == ("PROVIDER", "OK")
    assert dec(p, "pmc", "cost_basis", "value", "unrealized") == (
        Decimal("97.6"), Decimal(4880), Decimal(4910), Decimal(30),
    )  # fmt: skip


# --- isolation (D-116) ------------------------------------------------------------------------


def everything_alice_reads(client: TestClient, account: dict, asset: dict) -> str:
    """Every read route open to Alice, as one text."""
    paths = [
        "/api/v1/me",
        "/api/v1/accounts",
        f"/api/v1/accounts/{account['id']}",
        f"/api/v1/accounts/{account['id']}/summary",
        "/api/v1/portfolio/summary",
        "/api/v1/assets",
        f"/api/v1/assets/{asset['id']}",
        f"/api/v1/assets/{asset['id']}/prices",
        "/api/v1/transactions",
        "/api/v1/imports",
    ]
    found = {path: get(client, path) for path in paths}
    found["positions"] = get(
        client, f"/api/v1/accounts/{account['id']}/positions", include_closed=True
    )
    return json.dumps(found, sort_keys=True)


def test_no_instrument_and_nothing_of_another_user_reaches_a_response(
    client: TestClient, account
) -> None:
    isin = fake_isin(42)
    mine = post(client, "/api/v1/assets", ETF | {"isin": isin})
    tx(client, account, "BUY", "2025-06-02", asset_id=mine["id"], quantity="10",
       unit_price="10.00", gross_amount="100.00", total_amount="100.00",
       affects_cash=False)  # fmt: skip
    provider(client, isin, {"2026-01-12": "10.10", "2026-01-14": "10.40"})
    before = everything_alice_reads(client, account, mine)

    bob = investor(client, "bob")
    bobs_account = post(client, "/api/v1/accounts", {"name": "Bob's broker",
                                                     "currency": "EUR"}, bob)  # fmt: skip
    his = post(client, "/api/v1/assets", ETF | {"name": "Bob's own name", "ticker": "BOBX",
                                                "isin": isin}, bob)  # fmt: skip
    tx(client, bobs_account, "BUY", "2025-07-01", headers=bob, asset_id=his["id"],
       quantity="3", unit_price="77.77", gross_amount="233.31", total_amount="233.31",
       affects_cash=False)  # fmt: skip
    price(client, his, "2026-01-14", "77.77", headers=bob)  # same day as a provider close
    link_assets(client.app.state.engine)  # Bob's asset joins Alice's instrument

    after = everything_alice_reads(client, account, mine)
    assert after == before  # nothing Alice reads depends on Bob

    with db_transaction(client.app.state.engine, write=False) as session:
        instrument = session.scalars(select(models.Instrument)).one()
        listing = session.scalars(select(models.InstrumentListing)).one()
        bob_user = session.scalars(select(models.User).where(models.User.username == "bob")).one()
        assert {a.instrument_id for a in session.scalars(select(models.Asset))} == {instrument.id}
    leaks = [
        str(instrument.id), str(listing.id), "XMPW.MI", "yahoo",  # the shared series
        str(bob_user.id), "bob", bobs_account["id"], "Bob's broker", his["id"],
        "Bob's own name", "BOBX", "77.77", "233.31",  # everything of Bob's
    ]  # fmt: skip
    assert [text for text in leaks if text in after] == []
