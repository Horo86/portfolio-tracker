"""`POST /transactions/batch/preview` and `POST /transactions/batch` (D-147): up to 50 creates
previewed with their amounts completed, then stored all or nothing, with errors that name
their row. Invented data only; the test clock says 2026-01-15."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, bearer, create_admin, fake_isin, investor, login_token
from test_api_transactions_preview import ETF, post, stored, trade

from portfolio_tracker.repositories import audit

BATCH_PREVIEW = "/api/v1/transactions/batch/preview"
BATCH = "/api/v1/transactions/batch"


@pytest.fixture
def setup(client: TestClient) -> dict:
    """An admin (cookie session) with one EUR account, an ETF, a bond and a USD ETF."""
    create_admin(client)
    bond = {"name": "Example Bond 2030", "asset_type": "BOND", "quote_currency": "EUR",
            "quote_unit": "PERCENT_OF_NOMINAL"}  # fmt: skip
    return {
        "account": post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"}),
        "asset": post(client, "/api/v1/assets", ETF | {"isin": fake_isin(20)}),
        "bond": post(client, "/api/v1/assets", bond | {"isin": fake_isin(21)}),
        "usd": post(client, "/api/v1/assets", ETF | {"name": "US ETF", "quote_currency": "USD"}),
    }


def held(setup, quantity: str, price: str, asset: str = "asset", **extra) -> dict:
    """A row of "add holdings I already own": a BUY that does not move cash, no amounts."""
    return trade(setup, "BUY", "2025-12-31", quantity, price, asset=asset,
                 affects_cash=False, **extra)  # fmt: skip


def batch_preview(client, rows, headers=CSRF):
    return client.post(BATCH_PREVIEW, headers=headers, json={"transactions": rows})


def previewed(client, rows) -> dict:
    response = batch_preview(client, rows)
    assert response.status_code == 200, response.text
    return response.json()


def row_issues(response) -> dict[int, set[tuple[str, str]]]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    return {
        tx["index"]: {(i["field"], i["code"]) for i in tx["issues"]} for tx in body["transactions"]
    }


# --- preview ------------------------------------------------------------------------------


def test_each_row_is_completed_and_nothing_is_stored(client: TestClient, setup) -> None:
    before = stored(client)
    rows = [
        held(setup, "19", "102.00"),
        held(setup, "5000", "97.50", asset="bond"),  # nominal and % of nominal (D-66)
        held(setup, "30", "94.10", asset="usd", gross_amount="2600.00"),  # the broker's cost
    ]

    out = previewed(client, rows)

    amounts = [(t["gross_amount"], t["total_amount"]) for t in out["transactions"]]
    assert amounts == [("1938.00", "1938.00"), ("4875.00", "4875.00"), ("2600.00", "2600.00")]
    assert out["derived"] == [["gross_amount", "total_amount"]] * 2 + [["total_amount"]]
    assert {k: out["transactions"][0][k] for k in rows[0]} == rows[0]  # sent fields as sent
    assert all(t["affects_cash"] is False for t in out["transactions"])
    assert out["warnings"] == []
    assert stored(client) == before


def test_field_rules_are_reported_for_every_row_at_once(client: TestClient, setup) -> None:
    rows = [
        held(setup, "19", "102.00"),
        held(setup, "19", "102.00") | {"quantity": None},
        held(setup, "30", "94.10", asset="usd"),  # another currency: the broker's cost needed
    ]
    assert row_issues(batch_preview(client, rows)) == {
        1: {("quantity", "required"), ("gross_amount", "required"), ("total_amount", "required")},
        2: {("gross_amount", "required"), ("total_amount", "required")},
    }


def test_a_derived_amount_too_long_names_its_row(client: TestClient, setup) -> None:
    rows = [held(setup, "1", "10"), held(setup, "999999999999999", "10")]
    assert row_issues(batch_preview(client, rows)) == {
        1: {("gross_amount", "too_many_digits"), ("total_amount", "too_many_digits")}
    }


def test_a_quantity_conflict_names_its_row(client: TestClient, setup) -> None:
    rows = [held(setup, "10", "100"), trade(setup, "SELL", "2025-12-31", "15", "100")]
    response = batch_preview(client, rows)
    assert response.status_code == 409, response.text
    conflict = response.json()["conflict"]
    assert (conflict["index"], conflict["quantity"], conflict["held"]) == (1, "-5", "10")


def test_two_rows_on_the_same_security_are_two_purchases(client: TestClient, setup) -> None:
    out = previewed(client, [held(setup, "10", "100"), held(setup, "5", "130")])
    assert [t["total_amount"] for t in out["transactions"]] == ["1000", "650"]


def test_a_warning_on_a_new_row_names_its_row(client: TestClient, setup) -> None:
    withdrawal = {"account_id": setup["account"]["id"], "type": "WITHDRAWAL",
                  "trade_date": "2025-01-02", "total_amount": "5.00"}  # fmt: skip
    post(client, "/api/v1/transactions", withdrawal)
    future = trade(setup, "BUY", "2026-02-01", "1", "10", affects_cash=False)

    out = previewed(client, [held(setup, "1", "10"), future])

    warnings = {(w["code"], w["index"], w["transaction_id"], w["introduced"])
                for w in out["warnings"]}  # fmt: skip
    assert warnings == {
        ("NEGATIVE_CASH", None, None, False),  # already there, about a day, not a new row
        ("FUTURE_DATE", 1, None, True),  # about the second row, which has no id yet
    }


@pytest.mark.parametrize(("count", "status"), [(50, 200), (51, 422), (0, 422)])
def test_one_to_fifty_rows(client: TestClient, setup, count: int, status: int) -> None:
    response = batch_preview(client, [held(setup, "1", "10")] * count)
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["code"] == "REQUEST_INVALID"
    else:
        assert len(response.json()["transactions"]) == 50


def test_unknown_fields_are_rejected(client: TestClient, setup) -> None:
    response = client.post(BATCH_PREVIEW, headers=CSRF, json={"transactions": [], "x": 1})
    assert response.status_code == 422


def test_a_cookie_session_needs_the_csrf_header(client: TestClient, setup) -> None:
    response = batch_preview(client, [held(setup, "1", "10")], headers={})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_CHECK_FAILED"


def test_a_bearer_session_needs_no_csrf_header(client: TestClient, setup) -> None:
    token = login_token(client, "admin")
    response = batch_preview(client, [held(setup, "1", "10")], headers=bearer(token))
    assert response.status_code == 200, response.text


def test_another_users_data_is_not_found(client: TestClient, setup) -> None:
    bob = investor(client, "bob")
    bobs = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, headers=bob)
    his = post(client, "/api/v1/assets", ETF | {"name": "Bob's ETF"}, headers=bob)
    mine = {"account_id": bobs["id"], "asset_id": his["id"]}
    before = stored(client)

    attempts = [
        [held(setup, "1", "10")],  # Alice's account and asset
        [held(setup, "1", "10") | mine, held(setup, "1", "10") | {"account_id": bobs["id"]}],
        [held(setup, "1", "10") | mine, held(setup, "1", "10") | {"asset_id": his["id"]}],
    ]
    for rows in attempts:
        response = batch_preview(client, rows, headers=bob)
        assert response.status_code == 404, (rows, response.text)
        assert response.json()["code"] == "NOT_FOUND"
    assert stored(client) == before


def test_never_takes_the_write_lock(client: TestClient, setup) -> None:
    """Another connection holds the SQLite write lock: the preview answers at once."""
    holder = sqlite3.connect(client.app.state.engine.url.database, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        out = previewed(client, [held(setup, "2", "10")])
        assert time.monotonic() - started < 5
        assert out["transactions"][0]["total_amount"] == "20"
    finally:
        holder.execute("ROLLBACK")
        holder.close()


# --- create -------------------------------------------------------------------------------


def batch(client, rows, headers=CSRF):
    return client.post(BATCH, headers=headers, json={"transactions": rows})


def completed(client, setup, *rows) -> list[dict]:
    """The rows as "add holdings I already own" saves them: through the batch preview."""
    return previewed(client, list(rows))["transactions"]


def test_the_previewed_rows_are_stored_as_previewed(client: TestClient, setup) -> None:
    rows = completed(client, setup, held(setup, "19", "102.00"),
                     held(setup, "5000", "97.50", asset="bond"))  # fmt: skip
    txs_before, audit_before = stored(client)

    response = batch(client, rows)

    assert response.status_code == 201, response.text
    out = response.json()
    assert [
        {k: tx[k] for k in row} for tx, row in zip(out["transactions"], rows, strict=True)
    ] == rows
    assert [tx["sequence"] for tx in out["transactions"]] == [1, 2]  # in request order
    txs, audits = stored(client)
    assert (len(txs), audits) == (len(txs_before) + 2, audit_before + 2)  # one entry each
    summary = client.get(f"/api/v1/accounts/{setup['account']['id']}/summary").json()
    assert summary["cash"] == "0"  # nothing moved cash


def test_both_amounts_are_required_as_on_post(client: TestClient, setup) -> None:
    rows = completed(client, setup, held(setup, "1", "10"))
    response = batch(client, [rows[0], held(setup, "1", "10")])
    assert row_issues(response) == {1: {("gross_amount", "required"), ("total_amount", "required")}}


def _broken(setup, bob_ids) -> dict[str, dict]:
    """A valid first row, then a second row that fails in each way."""
    return {
        "field rule": held(setup, "1", "10", gross_amount="10", total_amount="11"),
        "oversell": trade(
            setup, "SELL", "2025-12-31", "5", "10", gross_amount="50", total_amount="50"
        ),
        "another user's asset": held(
            setup, "1", "10", asset_id=bob_ids["asset"], gross_amount="10", total_amount="10"
        ),
        "another user's account": held(
            setup, "1", "10", account_id=bob_ids["account"], gross_amount="10", total_amount="10"
        ),
    }


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [("field rule", 422, "VALIDATION_FAILED"), ("oversell", 409, "QUANTITY_CONFLICT"),
     ("another user's asset", 404, "NOT_FOUND"), ("another user's account", 404, "NOT_FOUND")],
)  # fmt: skip
def test_nothing_is_stored_when_one_row_fails(client: TestClient, setup, case, status, code):
    bob = investor(client, "bob")
    bob_ids = {
        "account": post(
            client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, headers=bob
        )["id"],
        "asset": post(client, "/api/v1/assets", ETF | {"name": "Bob's"}, headers=bob)["id"],
    }
    good = completed(client, setup, held(setup, "1", "10"))[0]
    before = stored(client)

    response = batch(client, [good, _broken(setup, bob_ids)[case]])

    assert (response.status_code, response.json()["code"]) == (status, code), response.text
    if status == 422:
        assert [tx["index"] for tx in response.json()["transactions"]] == [1]
    if status == 409:
        assert response.json()["conflict"]["index"] == 1
    assert stored(client) == before


def test_a_failure_while_writing_stores_no_row(client: TestClient, setup, monkeypatch) -> None:
    """The rows are flushed, then the second audit entry fails: the request's transaction
    rolls back as a whole, so not even the first row stays."""
    rows = completed(client, setup, held(setup, "1", "10"), held(setup, "2", "10"))
    before = stored(client)
    record, calls = audit.record, []

    def failing(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 2:
            raise RuntimeError("disk full (simulated)")
        return record(*args, **kwargs)

    monkeypatch.setattr(audit, "record", failing)
    with pytest.raises(RuntimeError, match="simulated"):
        batch(client, rows)
    monkeypatch.undo()

    assert calls == ["transaction.created", "transaction.created"]
    assert stored(client) == before


@pytest.mark.parametrize(("count", "status"), [(50, 201), (51, 422), (0, 422)])
def test_one_to_fifty_rows_are_created(client: TestClient, setup, count, status) -> None:
    row = completed(client, setup, held(setup, "1", "10"))[0]
    txs_before, audit_before = stored(client)

    response = batch(client, [row] * count)

    assert response.status_code == status, response.text
    txs, audits = stored(client)
    if status == 201:
        assert len(response.json()["transactions"]) == 50
        assert (len(txs), audits) == (len(txs_before) + 50, audit_before + 50)
    else:
        assert response.json()["code"] == "REQUEST_INVALID"
        assert (txs, audits) == (txs_before, audit_before)


def test_a_cookie_session_needs_the_csrf_header_to_create(client: TestClient, setup) -> None:
    rows = completed(client, setup, held(setup, "1", "10"))
    before = stored(client)
    response = batch(client, rows, headers={})
    assert (response.status_code, response.json()["code"]) == (403, "CSRF_CHECK_FAILED")
    assert stored(client) == before


def test_a_bearer_session_creates_without_the_csrf_header(client: TestClient, setup) -> None:
    rows = completed(client, setup, held(setup, "1", "10"))
    response = batch(client, rows, headers=bearer(login_token(client, "admin")))
    assert response.status_code == 201, response.text
