"""Adding an asset from a search result, and linking through the queue (D-121, D-125, D-127):
only the ISIN and the quote currency reach the asset, the listing is chosen by the linking job,
and neither creating an asset nor confirming an import waits for the network. Invented data."""

import base64
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fakes import Provider
from fastapi.testclient import TestClient
from helpers import CSRF, fake_isin
from test_api_reports import ETF, post

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.repositories import instruments
from portfolio_tracker.services.scheduler import market_job

ISIN = fake_isin(418)
CHECK_DAY = datetime(2026, 3, 5, 18, 0, tzinfo=UTC)  # the synthetic closes are recent then


@pytest.fixture
def queued(on: TestClient, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The jobs queued by requests, recorded instead of run: the tests run them."""
    kinds: list[str] = []
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", kinds.append)
    return kinds


def run_linking_job(client: TestClient) -> None:
    market_data = client.app.state.market_data
    job = market_job(
        client.app.state.engine,
        market_data.registry,
        market_data.transport,
        market_data.cipher,
        urls=market_data.urls,
        clock=lambda: CHECK_DAY,
    )
    job("BACKFILL")


def link_of(client: TestClient, asset_id: str) -> tuple[str | None, str | None]:
    """(instrument id, the symbol of its active listing) of an asset."""
    with db_transaction(client.app.state.engine, write=False) as session:
        asset = session.get(models.Asset, UUID(asset_id))
        if asset.instrument_id is None:
            return None, None
        active = instruments.active_listing(session, asset.instrument_id)
        return str(asset.instrument_id), active.symbol if active else None


# --- point 1: the service chooses the listing ------------------------------------------------


def test_a_search_result_gives_only_its_isin_and_currency(
    on: TestClient, queued: list[str]
) -> None:
    """The user picks the Xetra line; the asset gets the ISIN and the currency only, and the
    linking job chooses the Milan listing by the rank of D-125."""
    results = on.post("/api/v1/assets/search", headers=CSRF, json={"q": ISIN}).json()
    picked = next(r for r in results if r["symbol"] == "XMPW.DE")
    body = ETF | {"isin": picked["isin"], "quote_currency": picked["currency"]}
    asset = post(on, "/api/v1/assets", body)
    assert queued == ["BACKFILL"] and link_of(on, asset["id"]) == (None, None)

    run_linking_job(on)

    assert link_of(on, asset["id"])[1] == "XMPW.MI"


@pytest.mark.parametrize("field", ["symbol", "listing_id", "exchange", "provider"])
def test_a_listing_cannot_be_named_when_adding_an_asset(on: TestClient, field: str) -> None:
    body = ETF | {"isin": ISIN, field: "XMPW.DE"}
    response = on.post("/api/v1/assets", headers=CSRF, json=body)
    assert response.status_code == 422, response.text


def test_linking_is_queued_only_for_an_isin_or_currency_change(
    on: TestClient, queued: list[str]
) -> None:
    manual = post(on, "/api/v1/assets", ETF)  # no ISIN: manual prices only
    assert queued == []
    on.patch(f"/api/v1/assets/{manual['id']}", headers=CSRF, json={"name": "Renamed"})
    assert queued == []
    response = on.patch(f"/api/v1/assets/{manual['id']}", headers=CSRF, json={"isin": ISIN})
    assert response.status_code == 200 and queued == ["BACKFILL"]


# --- point 4: the import stays offline -------------------------------------------------------

CSV = (
    "account,account_currency,trade_date,type,isin,asset_name,asset_type,asset_quote_currency,"
    "asset_quote_unit,quantity,unit_price,gross_amount,total_amount,affects_cash\n"
    f"Main,EUR,2025-06-02,BUY,{ISIN},Example World,ETF,EUR,CURRENCY,10,10.00,100.00,100.00,"
    "false\n"
)


def test_an_import_confirmation_never_waits_for_the_network(
    on: TestClient, answers: Provider, queued: list[str]
) -> None:
    def offline() -> None:
        raise AssertionError("the import called a provider")

    answers.during = offline
    body = {"content_base64": base64.b64encode(CSV.encode()).decode()}
    preview = post(on, "/api/v1/imports/preview", body, expected=200)
    done = post(on, "/api/v1/imports/confirm", body | {
        "fingerprint": preview["fingerprint"], "selected": ["line:2"],
    })  # fmt: skip

    assert answers.calls == [] and queued == ["BACKFILL"]
    [asset_id] = done["assets_created"]
    assert link_of(on, asset_id) == (None, None)  # linked by the job, not by the request

    answers.during = None
    run_linking_job(on)
    assert link_of(on, asset_id)[1] == "XMPW.MI"
