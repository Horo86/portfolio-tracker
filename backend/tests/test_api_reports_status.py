"""Positions with provider prices (D-116, D-119, D-134): which close values a position and
where it comes from, and the asset's price status in the positions API. Provider data is
stored directly (a linked instrument with a listing and closes). Invented data only."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from helpers import create_admin, fake_isin
from test_api_reports import BOND, ETF, get, post, price, tx

from portfolio_tracker.db import db_transaction
from portfolio_tracker.marketdata.base import PriceClass
from portfolio_tracker.repositories import instruments
from portfolio_tracker.services.market_data import link_assets

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def provider(
    client: TestClient,
    isin: str,
    closes: dict[str, str] | None = None,
    *,
    listing_status: str = "OK",
    mismatch: str | None = None,
    active: bool = True,
    check: str = "OK",
    postponed: bool = False,
    unit: str = "CURRENCY",
    price_class: PriceClass = PriceClass.ETF_STOCK,
) -> None:
    """Link the assets, then give the ISIN's instrument a class, one engine's check and,
    unless `active` is False, an active listing with `closes`."""
    engine = client.app.state.engine
    link_assets(engine)
    with db_transaction(engine, write=True) as session:
        instrument = instruments.get_instrument(session, isin, "EUR")
        instruments.set_price_class(session, instrument, price_class, "openfigi")
        if postponed:
            instrument.postponed_since = NOW
        symbol = "XMPW.MI" if check == "OK" else None
        instruments.save_check(
            session,
            instrument.id,
            "yahoo",
            status=check,
            symbol=symbol,
            mismatch="CURRENCY" if check == "MISMATCH" else None,
            checked_at=NOW,
        )
        if not active:
            return
        listing = instruments.save_listing(
            session,
            instrument.id,
            "yahoo",
            "XMPW.MI",
            currency="EUR",
            unit=unit,
            status=listing_status,
            mismatch=mismatch,
        )
        instruments.activate(session, listing, NOW)
        instruments.store_closes(
            session,
            listing.id,
            [
                instruments.StoredClose(date.fromisoformat(d), Decimal(c))
                for d, c in (closes or {}).items()
            ],
        )


@pytest.fixture
def account(client: TestClient) -> dict:
    create_admin(client)
    return post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})


def holding(client, account, body: dict) -> dict:
    asset = post(client, "/api/v1/assets", body)
    tx(client, account, "BUY", "2025-06-02", asset_id=asset["id"], quantity="10",
       unit_price="10.00", gross_amount="100.00", total_amount="100.00",
       affects_cash=False)  # fmt: skip
    return asset


def position(client, account) -> dict:
    [found] = get(client, f"/api/v1/accounts/{account['id']}/positions")
    return found


def test_my_manual_price_wins_on_its_day_and_a_newer_provider_close_after_it(
    client: TestClient, account, clock
) -> None:
    """domain-rules §12, D-116: manual 10.00 on 13 January; provider 9.90 that same day and
    10.40 on the 14th."""
    etf = holding(client, account, ETF | {"isin": fake_isin(31)})
    provider(client, fake_isin(31), {"2026-01-12": "9.80", "2026-01-13": "9.90",
                                     "2026-01-14": "10.40"})  # fmt: skip
    price(client, etf, "2026-01-13", "10.00")

    clock.now = datetime(2026, 1, 13, 18, 0, tzinfo=UTC)
    p = position(client, account)
    assert (p["price"], p["price_date"], p["price_source"]) == ("10.00", "2026-01-13", "MANUAL")

    clock.now = NOW
    p = position(client, account)
    assert (p["price"], p["price_date"], p["price_source"]) == ("10.40", "2026-01-14", "PROVIDER")
    assert (p["price_status"], p["price_status_detail"]) == ("OK", None)
    assert Decimal(p["value"]) == Decimal(104)


def test_a_position_without_any_close_has_no_source(client: TestClient, account) -> None:
    holding(client, account, ETF | {"isin": fake_isin(32)})
    p = position(client, account)
    assert (p["price_source"], p["estimated_at_cost"]) == (None, True)
    assert p["price_status"] == "PENDING"  # not linked yet: the next run looks it up


@pytest.mark.parametrize(
    ("body", "setup", "status", "detail"),
    [
        (ETF, None, "MANUAL", None),  # no ISIN: manual prices only
        (ETF | {"isin": fake_isin(33)}, {}, "OK", None),
        (ETF | {"isin": fake_isin(34)}, {"listing_status": "FAILING"}, "FAILING", None),
        (
            ETF | {"isin": fake_isin(35)},
            {"listing_status": "MISMATCH", "mismatch": "UNIT"},
            "MISMATCH",
            "UNIT",
        ),
        (BOND | {"isin": fake_isin(36)}, {}, "MISMATCH", "UNIT"),  # a bond from a per-unit line
        (
            ETF | {"isin": fake_isin(37)},
            {"active": False, "check": "NOT_FOUND"},
            "NOT_AVAILABLE",
            None,
        ),
        (ETF | {"isin": fake_isin(38)}, {"active": False, "check": "FAILING"}, "PENDING", None),
        (
            ETF | {"isin": fake_isin(39)},
            {"active": False, "check": "NOT_FOUND", "postponed": True},
            "PENDING",
            None,
        ),  # a class is stored, but the search waits for the next run
    ],
    ids=[
        "manual",
        "ok",
        "failing",
        "mismatch",
        "asset-unit",
        "not-available",
        "searching",
        "postponed",
    ],
)
def test_the_price_status_of_each_position(
    client: TestClient, account, body, setup, status, detail
) -> None:
    holding(client, account, body)
    if setup is not None:
        provider(client, body["isin"], {"2026-01-14": "10.20"}, **setup)
    p = position(client, account)
    assert (p["price_status"], p["price_status_detail"]) == (status, detail)
