"""Asset search (D-128): the provider's results only, nothing about other users, 20 searches
a minute per user, market data off, no typed text in the logs, no database transaction held
while the provider answers. A fake transport serves invented provider answers."""

import logging

import pytest
from fakes import Provider
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from test_api_reports import ETF, post, tx
from test_api_reports_status import provider

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError

ISIN = fake_isin(418)  # invented; the fake provider answers the same for any query
FIELDS = {"name", "symbol", "exchange", "currency", "quote_unit", "asset_type", "isin"}
TYPED = "Secret Holding"


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


def test_results_never_depend_on_what_another_user_holds(
    on: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point 2: nothing stored is read, so Bob holding the ISIN (linked, priced) changes
    nothing in Alice's results."""
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
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
    # sent to the provider as its query parameter, never in the URL path (FakeRequest.url has
    # no parameters, so the second check alone could not fail: final review N-3)
    assert answers.searches[0].params["q"] == TYPED and TYPED not in answers.searches[0].url


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


def test_a_provider_paused_until_the_next_run_gives_no_retry_after(on: TestClient) -> None:
    """Review N-4: after repeated failures the engine's throttle refuses calls until the next
    run; retrying in a few seconds would fail again, so no Retry-After is offered."""
    throttle = on.app.state.market_data.registry.throttle("yahoo")
    for _ in range(throttle.policy.stop_after_failures):
        throttle._failed(ProviderError(ErrorCode.NETWORK, "timeout"))
    response = on.post("/api/v1/assets/search", headers=CSRF, json={"q": "Example"})
    assert response.status_code == 503 and response.json()["code"] == "MARKET_DATA_UNAVAILABLE"
    assert "Retry-After" not in response.headers
