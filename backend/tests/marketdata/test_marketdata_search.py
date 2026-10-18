"""Asset search (D-128): the provider's results only, nothing about other users, 20 searches
a minute per user, market data off, no typed text in the logs, no database transaction held
while the provider answers. A fake transport serves invented provider answers."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fakes import FakeRequest, FakeTransport, fixture_response, json_response
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from test_api_reports import ETF, post, tx
from test_api_reports_status import provider

from portfolio_tracker.config import Settings
from portfolio_tracker.db import db_transaction
from portfolio_tracker.main import create_app
from portfolio_tracker.marketdata.registry import default_registry
from portfolio_tracker.repositories import job_runs

ISIN = fake_isin(418)  # invented; the fake provider answers the same for any query
CHARTS = {"XMPW.MI": "eur_milan", "XMPW.DE": "eur_etf", "XMPW.L": "gbp_pence"}
FIELDS = {"name", "symbol", "exchange", "currency", "quote_unit", "asset_type", "isin"}
TYPED = "Secret Holding"


class Provider:
    """Yahoo, OpenFIGI and Frankfurter as invented answers; `during` runs inside each call."""

    def __init__(self) -> None:
        self.during = None
        self.searches: list[FakeRequest] = []
        self.status = 200

    def __call__(self, request: FakeRequest):
        if self.during:
            self.during()
        if request.url.endswith("/v1/finance/search"):
            self.searches.append(request)
            if self.status != 200:
                return json_response({}, status=self.status, Retry_After="30")
            return fixture_response("yahoo_search_isin.json")
        if "/v8/finance/chart/" in request.url:
            name = CHARTS.get(request.url.rsplit("/", 1)[1], "not_found")
            return fixture_response(f"yahoo_chart_{name}.json", 404 if name == "not_found" else 200)
        return json_response([])  # anything a background job asks meanwhile


class InstantTime:
    """The throttles' monotonic clock: a wait advances it at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def answers() -> Provider:
    return Provider()


@pytest.fixture
def on(tmp_path: Path, fast_passwords, clock, answers) -> Iterator[TestClient]:
    """The app with market data on, provider throttles that never sleep, and a run already
    logged today, so no catch-up job runs beside the tests."""
    settings = Settings(data_dir=tmp_path / "data", market_data=True)
    time = InstantTime()
    first = TestClient(create_app(settings.model_copy(update={"market_data": False})))
    with first, db_transaction(first.app.state.engine, write=True) as session:
        run = job_runs.start_run(session, "DAILY", clock.now)
        job_runs.finish_run(session, run.id, result="OK", at=clock.now, counts={}, errors={})
    app = create_app(
        settings,
        passwords=fast_passwords,
        clock=clock,
        market_transport=FakeTransport(answers),
        market_registry=default_registry(time.clock, time.sleep),
    )
    with TestClient(app, base_url="https://testserver") as client:
        create_admin(client)
        yield client


def search(client: TestClient, q: str, headers=CSRF, expected: int = 200):
    response = client.post("/api/v1/assets/search", headers=headers, json={"q": q})
    assert response.status_code == expected, response.text
    return response.json()


def test_an_isin_search_gives_the_providers_listings_with_that_isin(on: TestClient) -> None:
    results = search(on, ISIN.lower())
    assert [(r["symbol"], r["exchange"], r["currency"], r["quote_unit"]) for r in results] == [
        ("XMPW.MI", "Milan", "EUR", "CURRENCY"),
        ("XMPW.DE", "XETRA", "EUR", "CURRENCY"),
        ("XMPW.L", "London", "GBP", "MINOR_CURRENCY_UNIT"),
    ]
    assert {r["isin"] for r in results} == {ISIN} and {r["asset_type"] for r in results} == {"ETF"}
    assert all(set(r) == FIELDS for r in results)  # no "already tracked", no count


def test_a_text_search_has_no_isin(on: TestClient) -> None:
    results = search(on, "Example World")
    assert results and {r["isin"] for r in results} == {None}


def test_results_never_depend_on_what_another_user_holds(on: TestClient) -> None:
    """Point 2: nothing stored is read, so Bob holding the ISIN (linked, priced) changes
    nothing in Alice's results."""
    alice = investor(on, "alice")
    before = search(on, ISIN, alice)

    bob = investor(on, "bob")
    account = post(on, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, bob)
    asset = post(on, "/api/v1/assets", ETF | {"isin": ISIN}, bob)
    tx(on, account, "BUY", "2025-06-02", headers=bob, asset_id=asset["id"], quantity="1",
       unit_price="10.00", gross_amount="10.00", total_amount="10.00",
       affects_cash=False)  # fmt: skip
    provider(on, ISIN, {"2026-01-14": "10.20"})

    assert search(on, ISIN, alice) == before


# --- traffic (point 3) ---------------------------------------------------------------------------


def test_twenty_searches_a_minute_per_user(on: TestClient, clock) -> None:
    for _ in range(20):
        search(on, "Example")
    response = on.post("/api/v1/assets/search", headers=CSRF, json={"q": "Example"})
    assert response.status_code == 429 and response.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert response.headers["Retry-After"] == "60"

    search(on, "Example", investor(on, "bob"))  # another user has their own limit
    clock.advance(seconds=30)
    assert (
        on.post("/api/v1/assets/search", headers=CSRF, json={"q": "x y"}).headers["Retry-After"]
        == "30"
    )
    clock.advance(seconds=30)
    search(on, "Example")  # the first searches left the window


def test_with_market_data_off_search_answers_so_and_calls_nothing(client: TestClient) -> None:
    create_admin(client)
    response = client.post("/api/v1/assets/search", headers=CSRF, json={"q": ISIN})
    assert response.status_code == 503 and response.json()["code"] == "MARKET_DATA_OFF"


def test_the_typed_text_never_reaches_the_logs(
    on: TestClient, answers: Provider, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        search(on, TYPED)
        answers.status = 500
        failed = on.post("/api/v1/assets/search", headers=CSRF, json={"q": TYPED})
    assert failed.status_code == 503 and failed.json()["code"] == "MARKET_DATA_UNAVAILABLE"
    assert TYPED not in failed.text
    logged = caplog.text + "".join(repr(r.__dict__) for r in caplog.records)
    assert "asset search failed" in caplog.text and TYPED not in logged
    assert TYPED not in str(answers.searches[0].url)  # in the provider's query, not a URL path


def test_a_busy_provider_gives_retry_after(on: TestClient, answers: Provider) -> None:
    answers.status = 429
    response = on.post("/api/v1/assets/search", headers=CSRF, json={"q": "Example"})
    assert response.status_code == 503 and response.json()["code"] == "MARKET_DATA_BUSY"
    assert response.headers["Retry-After"] == "30"


def test_no_database_transaction_is_open_while_the_provider_answers(
    on: TestClient, answers: Provider
) -> None:
    pool = on.app.state.engine.pool
    seen: list[int] = []
    answers.during = lambda: seen.append(pool.checkedout())
    search(on, ISIN)
    assert seen and set(seen) == {0}


@pytest.mark.parametrize("q", ["x", "", "y" * 41])
def test_a_query_has_2_to_40_characters(on: TestClient, q: str) -> None:
    search(on, q, expected=422)
