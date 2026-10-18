"""The Administrator's market data routes (D-121, D-135): run now and the status. 404 for
everyone else, counts and error codes only: never an ISIN, an instrument, a listing or a
symbol. Invented data only."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import select
from test_api_reports import ETF, post

from portfolio_tracker.db import db_transaction, models
from portfolio_tracker.services.scheduler import Trigger, market_job

RUN, STATUS = "/api/v1/admin/market-data/run", "/api/v1/admin/market-data/status"
ISIN = fake_isin(418)


def link_now(client: TestClient) -> None:
    market_data = client.app.state.market_data
    market_job(
        client.app.state.engine,
        market_data.registry,
        market_data.transport,
        market_data.cipher,
        urls=market_data.urls,
        clock=lambda: datetime(2026, 3, 5, 18, 0, tzinfo=UTC),
    )("DAILY")


def test_an_investor_gets_404_on_both_routes(on: TestClient) -> None:
    alice = investor(on, "alice")
    for method, path in (("POST", RUN), ("GET", STATUS)):
        response = on.request(method, path, headers=alice)
        assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize(
    ("trigger", "status", "answer"),
    [
        (Trigger.QUEUED, 202, {"queued": "QUEUED"}),
        (Trigger.ALREADY_QUEUED, 202, {"queued": "ALREADY_QUEUED"}),
        (Trigger.BUSY, 409, None),
    ],
)
def test_run_now_queues_a_run_or_is_refused_while_one_runs(
    on: TestClient, monkeypatch: pytest.MonkeyPatch, trigger, status, answer
) -> None:
    asked: list[str] = []
    scheduler = on.app.state.market_data.scheduler
    monkeypatch.setattr(scheduler, "trigger", lambda kind: asked.append(kind) or trigger)

    response = on.post(RUN, headers=CSRF)

    assert response.status_code == status and asked == ["MANUAL"]
    if answer is None:
        assert response.json()["code"] == "MARKET_DATA_RUNNING"
        return
    assert response.json() == answer
    with db_transaction(on.app.state.engine, write=False) as session:
        actions = [e.action for e in session.scalars(select(models.AuditEntry))]
    assert actions[-1] == "market_data.run_requested"


def test_with_market_data_off_run_now_is_refused(client: TestClient) -> None:
    create_admin(client)
    response = client.post(RUN, headers=CSRF)
    assert (response.status_code, response.json()["code"]) == (409, "MARKET_DATA_OFF")
    status = client.get(STATUS).json()
    assert (status["market_data"], status["next_run_at"], status["running"]) == ("OFF", None, None)


def test_the_status_holds_counts_only(on: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    alice = investor(on, "alice")
    asset = post(on, "/api/v1/assets", ETF | {"isin": ISIN}, alice)
    link_now(on)

    response = on.get(STATUS)
    assert response.status_code == 200
    status = response.json()
    assert status["market_data"] == "ON" and status["daily_time_utc"] == "22:00:00"
    assert status["next_run_at"] == "2026-01-15T22:00:00Z"
    assert status["last_run"]["kind"] == "DAILY"
    assert status["last_run"]["counts"]["assets_linked"] == 1
    assert status["last_run"]["counts"]["listings_chosen"] == 1
    assert {e["name"]: e["checks"] for e in status["engines"]} == {"yahoo": {"OK": 1}}

    with db_transaction(on.app.state.engine, write=False) as session:
        instrument = session.scalars(select(models.Instrument)).one()
        listing = session.scalars(select(models.InstrumentListing)).one()
        assert session.get(models.Asset, UUID(asset["id"])).instrument_id == instrument.id
    for secret in (ISIN, str(instrument.id), str(listing.id), "XMPW", asset["id"], "alice"):
        assert secret not in response.text, secret
