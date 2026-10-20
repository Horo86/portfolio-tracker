"""`POST /transactions/preview`: a dry run that completes the amounts and reports cash and
position before and after, with the errors of the write routes and nothing stored (D-139).
Also the `held` quantity of a QUANTITY_CONFLICT (D-139). Invented data only; the test clock
says 2026-01-15."""

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import AuditEntry, Transaction

PREVIEW = "/api/v1/transactions/preview"
ETF = {"name": "Example ETF", "asset_type": "ETF", "quote_currency": "EUR",
       "quote_unit": "CURRENCY"}  # fmt: skip
BOND = {"name": "Example Bond 2030", "asset_type": "BOND", "quote_currency": "EUR",
        "quote_unit": "PERCENT_OF_NOMINAL"}  # fmt: skip


@pytest.fixture
def setup(client: TestClient) -> dict:
    """An admin (cookie session) with one EUR account, one ETF and one bond."""
    create_admin(client)
    return {
        "account": post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"}),
        "asset": post(client, "/api/v1/assets", ETF | {"isin": fake_isin(10)}),
        "bond": post(client, "/api/v1/assets", BOND | {"isin": fake_isin(11)}),
    }


def post(client, path, body, headers=CSRF, expected=201) -> dict:
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def preview(client, body, headers=CSRF):
    return client.post(PREVIEW, headers=headers, json=body)


def previewed(client, body) -> dict:
    response = preview(client, body)
    assert response.status_code == 200, response.text
    return response.json()


def trade(setup, type_, day, quantity, price, asset="asset", **extra) -> dict:
    """What the form sends: no gross_amount, no total_amount."""
    body = {"account_id": setup["account"]["id"], "type": type_, "trade_date": day,
            "asset_id": setup[asset]["id"], "quantity": quantity, "unit_price": price}  # fmt: skip
    return body | extra


def split(setup, day, ratio) -> dict:
    return {"account_id": setup["account"]["id"], "type": "STOCK_SPLIT", "trade_date": day,
            "asset_id": setup["asset"]["id"], "split_ratio": ratio}  # fmt: skip


def cash(setup, type_, day, amount) -> dict:
    return {"account_id": setup["account"]["id"], "type": type_, "trade_date": day,
            "total_amount": amount}  # fmt: skip


def save(client, body) -> dict:
    """Save a transaction through its preview, as the form does."""
    done = previewed(client, {"transaction": body})["transaction"]
    return post(client, "/api/v1/transactions", done)["transactions"][0]


def stored(client) -> tuple[list[tuple], int]:
    """Stored transactions and audit entries, to prove a preview writes nothing."""
    with Session(client.app.state.engine) as db:
        txs = [
            (str(r.id), r.account_id, r.type, r.sequence, r.quantity, r.gross_amount, r.updated_at)
            for r in db.scalars(select(Transaction).order_by(Transaction.id))
        ]
        return txs, db.scalar(select(func.count()).select_from(AuditEntry))


def issues(response) -> set[tuple[str, str]]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    [tx] = body["transactions"]
    assert tx["index"] == 0
    return {(i["field"], i["code"]) for i in tx["issues"]}


# --- create -----------------------------------------------------------------------------


def test_buy_is_completed_and_reported_without_being_stored(client: TestClient, setup) -> None:
    save(client, cash(setup, "DEPOSIT", "2025-01-02", "2000.00"))
    before = stored(client)

    out = previewed(client, {"transaction": trade(setup, "BUY", "2025-01-10", "15", "101.80",
                                                  fees="3.00")})  # fmt: skip

    assert out["transaction"]["gross_amount"] == "1527.00"
    assert out["transaction"]["total_amount"] == "1530.00"
    assert out["derived"] == ["gross_amount", "total_amount"]
    assert (out["account_id"], out["currency"]) == (setup["account"]["id"], "EUR")
    assert out["cash"] == {"before": "2000.00", "change": "-1530.00", "after": "470.00"}
    assert out["position"] == {
        "asset_id": setup["asset"]["id"],
        "quantity_before": "0", "quantity_after": "15",
        "cost_basis_before": "0", "cost_basis_after": "1530.00",
        "pmc_before": None, "pmc_after": "102.00000000",
    }  # fmt: skip
    assert out["warnings"] == []
    assert stored(client) == before


def test_the_completed_transaction_is_what_post_stores(client: TestClient, setup) -> None:
    body = trade(setup, "BUY", "2025-01-10", "0.12345678", "101.80", fees="1.00")
    done = previewed(client, {"transaction": body})["transaction"]
    assert done["gross_amount"] == "12.5679002040"  # exact, never rounded to cents (§0)
    assert done["total_amount"] == "13.5679002040"

    tx = post(client, "/api/v1/transactions", done)["transactions"][0]

    assert (tx["gross_amount"], tx["total_amount"]) == ("12.5679002040", "13.5679002040")
    assert {k: tx[k] for k in done} == done


def test_sell_keeps_the_average_cost(client: TestClient, setup) -> None:
    save(client, trade(setup, "BUY", "2025-01-10", "10", "100", fees="5.00"))

    out = previewed(client, {"transaction": trade(setup, "SELL", "2025-02-10", "4", "120",
                                                  taxes="20.80")})  # fmt: skip

    assert out["transaction"]["total_amount"] == "459.20"  # 480 - 20.80
    p = out["position"]
    assert (p["quantity_before"], p["quantity_after"]) == ("10", "6")
    assert (p["cost_basis_before"], p["cost_basis_after"]) == ("1005.00", "603.00000000")
    assert p["pmc_before"] == p["pmc_after"] == "100.50000000"
    assert out["cash"]["change"] == "459.20"


def test_bond_buy_e4(client: TestClient, setup) -> None:
    body = trade(setup, "BUY", "2025-03-03", "5000", "97.50", asset="bond", fees="5.00",
                 accrued_interest_paid="12.34", taxes="-2.44")  # fmt: skip

    out = previewed(client, {"transaction": body})

    assert out["transaction"]["gross_amount"] == "4875.00"
    assert out["transaction"]["total_amount"] == "4889.90"  # §16 E4
    assert out["position"]["cost_basis_after"] == "4880.00"  # accrued and taxes not in cost
    assert out["cash"]["change"] == "-4889.90"


def test_a_foreign_quote_currency_needs_the_brokers_gross(client: TestClient, setup) -> None:
    usd = post(client, "/api/v1/assets", ETF | {"name": "US ETF", "quote_currency": "USD"})
    body = trade(setup, "BUY", "2025-01-10", "10", "140.20") | {"asset_id": usd["id"]}

    assert issues(preview(client, {"transaction": body})) == {
        ("gross_amount", "required"), ("total_amount", "required")
    }  # fmt: skip

    out = previewed(client, {"transaction": body | {"gross_amount": "1291.10", "fees": "3.00"}})
    assert out["transaction"]["total_amount"] == "1294.10"
    assert out["derived"] == ["total_amount"]


def test_a_typed_gross_is_kept(client: TestClient, setup) -> None:
    body = trade(setup, "BUY", "2025-01-10", "3", "33.333", gross_amount="100.00")
    out = previewed(client, {"transaction": body})
    assert out["transaction"]["gross_amount"] == "100.00"
    assert out["derived"] == ["total_amount"]


def test_a_typed_total_that_breaks_the_invariant_is_reported(client: TestClient, setup) -> None:
    body = trade(setup, "BUY", "2025-01-10", "2", "10", total_amount="25.00")
    assert issues(preview(client, {"transaction": body})) == {("total_amount", "invariant")}


@pytest.mark.parametrize(
    ("quantity", "price"),
    [("0.12345678", "1.1234567890123"), ("999999999999999", "10")],  # 21 decimals; 16 digits
)
def test_a_derived_amount_too_long_to_save_must_be_typed(client, setup, quantity, price) -> None:
    body = trade(setup, "BUY", "2025-01-10", quantity, price)
    assert issues(preview(client, {"transaction": body})) == {
        ("gross_amount", "too_many_digits"), ("total_amount", "too_many_digits")
    }  # fmt: skip


def test_cash_movements_have_no_position(client: TestClient, setup) -> None:
    save(client, cash(setup, "DEPOSIT", "2025-01-02", "100.00"))

    out = previewed(client, {"transaction": cash(setup, "WITHDRAWAL", "2025-01-05", "150.00")})

    assert out["position"] is None
    assert out["derived"] == []
    assert out["cash"] == {"before": "100.00", "change": "-150.00", "after": "-50.00"}
    assert [(w["code"], w["date"], w["introduced"]) for w in out["warnings"]] == [
        ("NEGATIVE_CASH", "2025-01-05", True)
    ]


def test_a_cash_movement_without_amount_is_reported(client: TestClient, setup) -> None:
    body = cash(setup, "DEPOSIT", "2025-01-02", "1") | {"total_amount": None}
    assert issues(preview(client, {"transaction": body})) == {("total_amount", "required")}


def test_income_on_an_asset_never_held_warns(client: TestClient, setup) -> None:
    body = {"account_id": setup["account"]["id"], "type": "DIVIDEND", "trade_date": "2025-05-20",
            "asset_id": setup["asset"]["id"], "gross_amount": "20.00", "taxes": "5.20"}  # fmt: skip

    out = previewed(client, {"transaction": body})

    assert out["transaction"]["total_amount"] == "14.80"
    assert out["position"]["quantity_after"] == "0"
    assert [(w["code"], w["introduced"]) for w in out["warnings"]] == [
        ("INCOME_WITHOUT_HOLDING", True)
    ]


def test_a_future_date_warns(client: TestClient, setup) -> None:
    out = previewed(client, {"transaction": cash(setup, "DEPOSIT", "2026-02-01", "10.00")})
    assert [(w["code"], w["introduced"]) for w in out["warnings"]] == [("FUTURE_DATE", True)]


def test_split_changes_quantity_and_average_cost(client: TestClient, setup) -> None:
    save(client, trade(setup, "BUY", "2025-01-10", "10", "100"))
    out = previewed(client, {"transaction": split(setup, "2025-06-02", "2:1")})

    assert out["transaction"]["split_ratio"] == "2:1"
    assert out["derived"] == []
    p = out["position"]
    assert (p["quantity_after"], p["pmc_before"], p["pmc_after"]) == (
        "20.00000000", "100.00000000", "50.00000000"
    )  # fmt: skip
    assert out["cash"]["change"] == "0"


def test_an_oversell_is_a_conflict_with_the_held_quantity(client: TestClient, setup) -> None:
    save(client, trade(setup, "BUY", "2025-01-10", "26", "100"))
    before = stored(client)

    response = preview(client, {"transaction": trade(setup, "SELL", "2025-09-02", "30", "100")})

    assert response.status_code == 409, response.text
    conflict = response.json()["conflict"]
    assert conflict == {
        "reason": "NEGATIVE_QUANTITY", "index": 0, "account_id": setup["account"]["id"],
        "asset_id": setup["asset"]["id"], "quantity": "-4", "held": "26",
    }  # fmt: skip
    assert stored(client) == before


# --- edit and delete --------------------------------------------------------------------


def test_edit_preview_matches_what_put_stores(client: TestClient, setup) -> None:
    tx = save(client, trade(setup, "BUY", "2025-01-10", "10", "100"))
    before = stored(client)
    body = trade(setup, "BUY", "2025-01-10", "12", "100", fees="2.00")

    out = previewed(client, {"replaces": tx["id"], "transaction": body})

    assert stored(client) == before
    p = out["position"]
    assert (p["quantity_before"], p["quantity_after"]) == ("10", "12")
    assert out["cash"] == {"before": "-1000", "change": "-202.00", "after": "-1202.00"}
    assert [(w["code"], w["introduced"]) for w in out["warnings"]] == [("NEGATIVE_CASH", False)]
    saved = client.put(f"/api/v1/transactions/{tx['id']}", headers=CSRF, json=out["transaction"])
    assert saved.status_code == 200, saved.text
    assert {k: saved.json()["transactions"][0][k] for k in out["transaction"]} == out["transaction"]


def test_delete_preview_shows_the_quantity_left(client: TestClient, setup) -> None:
    save(client, trade(setup, "BUY", "2025-01-10", "25", "100"))
    second = save(client, trade(setup, "BUY", "2025-02-10", "5", "100"))
    before = stored(client)

    out = previewed(client, {"replaces": second["id"]})

    assert (out["transaction"], out["derived"]) == (None, [])
    p = out["position"]
    assert (p["asset_id"], p["quantity_before"], p["quantity_after"]) == (
        setup["asset"]["id"], "30", "25"
    )  # fmt: skip
    assert out["cash"]["change"] == "500"  # the scale of 5 x "100"
    assert stored(client) == before


def test_delete_preview_refused_when_a_later_sale_needs_it(client: TestClient, setup) -> None:
    first = save(client, trade(setup, "BUY", "2025-01-10", "10", "100"))
    sale = save(client, trade(setup, "SELL", "2025-02-10", "10", "100"))

    response = preview(client, {"replaces": first["id"]})

    assert response.status_code == 409, response.text
    conflict = response.json()["conflict"]
    assert (conflict["transaction_id"], conflict["quantity"], conflict["held"]) == (
        sale["id"], "-10", "0"
    )  # fmt: skip


def test_a_move_to_another_currency_is_refused(client: TestClient, setup) -> None:
    tx = save(client, cash(setup, "DEPOSIT", "2025-01-02", "10.00"))
    usd = post(client, "/api/v1/accounts", {"name": "Dollari", "currency": "USD"})
    body = cash(setup, "DEPOSIT", "2025-01-02", "10.00") | {"account_id": usd["id"]}

    response = preview(client, {"replaces": tx["id"], "transaction": body})

    assert response.status_code == 409
    assert response.json()["code"] == "CURRENCY_MISMATCH"


# --- request rules ----------------------------------------------------------------------


def test_needs_a_transaction_or_the_one_it_replaces(client: TestClient, setup) -> None:
    response = preview(client, {})
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_INVALID"


def test_unknown_fields_are_rejected(client: TestClient, setup) -> None:
    response = preview(client, {"transaction": cash(setup, "DEPOSIT", "2025-01-02", "1"), "x": 1})
    assert response.status_code == 422


def test_a_cookie_session_needs_the_csrf_header(client: TestClient, setup) -> None:
    response = preview(client, {"transaction": cash(setup, "DEPOSIT", "2025-01-02", "1")}, {})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_CHECK_FAILED"


def test_another_users_data_is_not_found(client: TestClient, setup) -> None:
    tx = save(client, trade(setup, "BUY", "2025-01-10", "10", "100"))
    bob = investor(client, "bob")
    bobs = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, headers=bob)
    before = stored(client)

    attempts = [
        {"replaces": tx["id"]},  # Alice's transaction
        {"transaction": cash(setup, "DEPOSIT", "2025-01-02", "1")},  # Alice's account
        {"transaction": trade(setup, "BUY", "2025-01-10", "1", "1") | {"account_id": bobs["id"]}},
    ]
    for body in attempts:
        response = preview(client, body, headers=bob)
        assert response.status_code == 404, (body, response.text)
        assert response.json()["code"] == "NOT_FOUND"
    assert stored(client) == before


def test_never_takes_the_write_lock(client: TestClient, setup) -> None:
    """Another connection holds the SQLite write lock: a write would wait (30 s busy
    timeout), the preview answers at once from a read transaction (WAL)."""
    save(client, cash(setup, "DEPOSIT", "2025-01-02", "100.00"))
    holder = sqlite3.connect(client.app.state.engine.url.database, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        out = previewed(client, {"transaction": cash(setup, "WITHDRAWAL", "2025-01-03", "1.00")})
        assert time.monotonic() - started < 5
        assert out["cash"]["after"] == "99.00"
    finally:
        holder.execute("ROLLBACK")
        holder.close()


# --- held in the conflicts of the write routes (D-139) -----------------------------------


def test_write_routes_report_the_held_quantity(client: TestClient, setup) -> None:
    first = save(client, trade(setup, "BUY", "2025-01-10", "10", "100"))
    sale = save(client, trade(setup, "SELL", "2025-02-10", "10", "100"))
    body = {k: first[k] for k in ("account_id", "type", "trade_date", "asset_id", "unit_price")}

    response = client.put(f"/api/v1/transactions/{first['id']}", headers=CSRF, json=body | {
        "quantity": "6", "gross_amount": "600.00", "total_amount": "600.00"})  # fmt: skip

    assert response.status_code == 409, response.text
    conflict = response.json()["conflict"]
    assert (conflict["transaction_id"], conflict["quantity"], conflict["held"]) == (
        sale["id"], "-4", "6"
    )  # fmt: skip


def test_a_split_to_zero_reports_the_held_quantity(client: TestClient, setup) -> None:
    save(client, trade(setup, "BUY", "2025-01-10", "0.00000001", "100"))
    body = split(setup, "2025-06-02", "1:2")

    response = client.post("/api/v1/transactions", headers=CSRF, json=body)

    assert response.status_code == 409, response.text
    conflict = response.json()["conflict"]
    assert (conflict["reason"], conflict["quantity"], conflict["held"]) == (
        "SPLIT_TO_ZERO", "0.00000000", "0.00000001"
    )  # fmt: skip
