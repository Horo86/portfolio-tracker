"""The test-only launcher of the end-to-end tests with market data on (`e2e_market_server.py`):
it refuses a real folder, its app reaches only the invented fakes, an ISIN without a gate
links to `OK` with the real clock, and a gated one stays `PENDING` until its gate file
exists."""

import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from e2e_market_server import (
    STOCK_ISIN,
    TEXT_SYMBOL,
    WORLD_ISIN,
    FakeMarket,
    Refused,
    build_app,
    main,
    recent_weekdays,
    settings_from_environment,
)
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin
from marketdata.fakes import FakeTransport
from test_api_reports import post

from portfolio_tracker.engine.model import QuoteUnit

POSITIONS = "/api/v1/portfolio/positions"


@pytest.fixture
def market(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMarket:
    data = tmp_path / ".data-e2e-market"
    monkeypatch.setenv("PT_DATA_DIR", str(data))
    return FakeMarket(data)


@pytest.fixture
def client(market: FakeMarket) -> Iterator[TestClient]:
    with TestClient(build_app(settings_from_environment(), market)) as client:
        create_admin(client)
        yield client


def until(condition: Callable[[], object], seconds: float = 10.0) -> object:
    deadline = time.monotonic() + seconds
    while not (result := condition()):
        assert time.monotonic() < deadline, "the linking job did not get there in time"
        time.sleep(0.05)
    return result


def holding(client: TestClient, isin: str, name: str) -> str:
    """An EUR asset with `isin`, bought once (so it has a position and a status)."""
    account = post(client, "/api/v1/accounts", {"name": f"Conto {name}", "currency": "EUR"})
    asset = post(client, "/api/v1/assets", {"name": name, "asset_type": "ETF", "isin": isin,
                 "quote_currency": "EUR", "quote_unit": "CURRENCY"})  # fmt: skip
    post(client, "/api/v1/transactions", {"account_id": account["id"], "type": "BUY",
         "trade_date": "2026-01-02", "asset_id": asset["id"], "quantity": "1",
         "unit_price": "100.00", "gross_amount": "100.00", "total_amount": "100.00",
         "affects_cash": False})  # fmt: skip
    return asset["id"]


def position(client: TestClient, asset_id: str) -> dict:
    body = client.get(POSITIONS).json()
    return next(p for p in body["positions"] if p["asset_id"] == asset_id)


def priced(client: TestClient, asset_id: str) -> dict:
    """The position once its listing is chosen and its closes are stored: the status is `OK`
    as soon as the listing is chosen, a moment before the same job stores the closes."""
    return until(lambda: (p := position(client, asset_id))["price"] is not None and p)


def last_close_day() -> str:
    return recent_weekdays(datetime.now(UTC).date())[-1].isoformat()


def search(client: TestClient, q: str) -> list[dict]:
    response = client.post("/api/v1/assets/search", headers=CSRF, json={"q": q})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_folder_not_named_data_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(Refused):
        settings_from_environment()
    assert main() == 2
    assert "starts with .data" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()


def test_market_data_is_on_and_answered_by_the_fakes_only(client: TestClient) -> None:
    settings = client.app.state.market_data
    assert settings.on and isinstance(settings.transport, FakeTransport)
    found = search(client, WORLD_ISIN)
    assert [(r["symbol"], r["currency"], r["quote_unit"], r["isin"]) for r in found] == [
        ("ZWLD.MI", "EUR", QuoteUnit.CURRENCY, WORLD_ISIN),
        ("ZWLD.DE", "EUR", QuoteUnit.CURRENCY, WORLD_ISIN),
        ("ZWLD.L", "GBP", QuoteUnit.MINOR_CURRENCY_UNIT, WORLD_ISIN),
    ]  # a request's search never waits for a gate
    assert [(r["symbol"], r["isin"]) for r in search(client, "Example")] == [(TEXT_SYMBOL, None)]
    assert search(client, fake_isin(8703)) == []  # an ISIN the fakes do not know
    hosts = {call.url.split("/")[2] for call in settings.transport.calls}
    assert hosts <= {"query2.finance.yahoo.com"}


def test_an_isin_without_a_gate_links_to_ok_with_the_real_clock(client: TestClient) -> None:
    stock = holding(client, STOCK_ISIN, "Example Market Industrial")
    row = priced(client, stock)
    assert (row["price_status"], row["price"], row["price_date"]) == (
        "OK",
        "24.75",
        last_close_day(),
    )


def test_a_gated_isin_is_pending_until_its_gate_file_exists(
    client: TestClient, market: FakeMarket
) -> None:
    world = holding(client, WORLD_ISIN, "Example Market World")
    assert market.waiting.wait(10), "the linking job never reached the gate"
    assert position(client, world)["price_status"] == "PENDING"
    (market.data_dir / "gate-ZWLD.MI").touch()
    row = priced(client, world)
    assert (row["price_status"], row["price"], row["price_date"]) == (
        "OK",
        "101.75",
        last_close_day(),
    )
