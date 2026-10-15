"""Transactions through the API: E6/E7 (domain-rules §16), the no-deadlock operations (D-06b),
moves between accounts, warnings, owner isolation. Invented data only."""

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import AuditEntry, Transaction

ETF = {
    "name": "Example ETF",
    "asset_type": "ETF",
    "quote_currency": "EUR",
    "quote_unit": "CURRENCY",
}
BOND = {
    "name": "Example Bond 2030",
    "asset_type": "BOND",
    "quote_currency": "EUR",
    "quote_unit": "PERCENT_OF_NOMINAL",
}


@pytest.fixture
def setup(client: TestClient) -> dict:
    """An admin (cookie session) with one EUR account and one ETF."""
    create_admin(client)
    return {
        "account": post(client, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"}),
        "asset": post(client, "/api/v1/assets", ETF | {"isin": fake_isin(10)}),
    }


def post(client, path, body, headers=CSRF, expected=201) -> dict:
    response = client.post(path, headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def buy(account, asset, day, qty, price="100", time=None, **extra) -> dict:
    gross = str(int(qty) * int(price)) + ".00"
    body = {
        "account_id": account["id"], "type": "BUY", "trade_date": day, "asset_id": asset["id"],
        "quantity": qty, "unit_price": price, "gross_amount": gross, "total_amount": gross,
    }  # fmt: skip
    if time:
        body["trade_time"] = time
    return body | extra


def sell(account, asset, day, qty, price="100", time=None, **extra) -> dict:
    return buy(account, asset, day, qty, price, time, **extra) | {"type": "SELL"}


def create(client, body, headers=CSRF) -> dict:
    return client.post("/api/v1/transactions", headers=headers, json=body)


def created(client, body, headers=CSRF) -> dict:
    response = create(client, body, headers)
    assert response.status_code == 201, response.text
    return response.json()["transactions"][0]


def edit(client, tx, **changes):
    body = {k: v for k, v in tx.items() if k not in ("id", "sequence", "created_at", "updated_at")}
    return client.put(f"/api/v1/transactions/{tx['id']}", headers=CSRF, json=body | changes)


def delete(client, tx_id):
    return client.delete(f"/api/v1/transactions/{tx_id}", headers=CSRF)


def rows(client) -> list[tuple]:
    """Stored transactions, to prove that a rejected change wrote nothing."""
    with Session(client.app.state.engine) as db:
        return [
            (str(r.id), str(r.account_id), r.type, r.trade_date.isoformat(), r.sequence,
             str(r.quantity), str(r.asset_id))
            for r in db.scalars(select(Transaction).order_by(Transaction.id))
        ]  # fmt: skip


def audit_count(client) -> int:
    with Session(client.app.state.engine) as db:
        return db.scalar(select(func.count()).select_from(AuditEntry))


def assert_conflict(response, transaction_id: str) -> None:
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "QUANTITY_CONFLICT"
    assert body["conflict"]["reason"] == "NEGATIVE_QUANTITY"
    assert body["conflict"]["transaction_id"] == transaction_id


# --- create and read --------------------------------------------------------------------


def test_create_and_read_a_buy(client: TestClient, setup) -> None:
    body = buy(setup["account"], setup["asset"], "2024-01-10", "10", fees="5.00")
    body["total_amount"] = "1005.00"

    response = create(client, body)

    assert response.status_code == 201
    tx = response.json()["transactions"][0]
    assert (tx["quantity"], tx["gross_amount"], tx["fees"], tx["total_amount"]) == (
        "10", "1000.00", "5.00", "1005.00",
    )  # fmt: skip
    assert tx["sequence"] == 1 and tx["affects_cash"] is True and tx["taxes"] is None
    assert client.get(f"/api/v1/transactions/{tx['id']}").json() == tx
    assert response.json()["warnings"] == [
        {
            "account_id": setup["account"]["id"], "code": "NEGATIVE_CASH",
            "date": "2024-01-10", "transaction_id": None, "introduced": True,
        }
    ]  # fmt: skip


def test_sequences_increase_per_account(client: TestClient, setup) -> None:
    other = post(client, "/api/v1/accounts", {"name": "Altro", "currency": "EUR"})
    deposit = {"type": "DEPOSIT", "trade_date": "2024-01-02", "total_amount": "100.00"}

    seqs = [
        created(client, deposit | {"account_id": acc["id"]})["sequence"]
        for acc in (setup["account"], setup["account"], other, setup["account"])
    ]

    assert seqs == [1, 2, 1, 3]


def test_bond_purchase_of_example_e4(client: TestClient, setup) -> None:
    bond = post(client, "/api/v1/assets", BOND | {"isin": fake_isin(11)})
    body = {
        "account_id": setup["account"]["id"], "type": "BUY", "trade_date": "2024-02-01",
        "asset_id": bond["id"], "quantity": "5000", "unit_price": "97.50",
        "gross_amount": "4875.00", "fees": "5.00", "accrued_interest_paid": "12.34",
        "taxes": "-2.44", "total_amount": "4889.90", "affects_cash": False,
    }  # fmt: skip

    tx = created(client, body)

    assert (tx["accrued_interest_paid"], tx["taxes"], tx["affects_cash"]) == (
        "12.34",
        "-2.44",
        False,
    )


def test_stock_split_ratio_round_trip(client: TestClient, setup) -> None:
    created(client, buy(setup["account"], setup["asset"], "2024-01-10", "10"))
    split = {
        "account_id": setup["account"]["id"], "type": "STOCK_SPLIT", "trade_date": "2024-03-01",
        "asset_id": setup["asset"]["id"], "split_ratio": "1:10",
    }  # fmt: skip

    assert created(client, split)["split_ratio"] == "1:10"


# --- field rules (422) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"total_amount": "999.99"}, {"field": "total_amount", "code": "invariant"}),
        ({"quantity": "0.123456789"}, {"field": "quantity", "code": "too_many_decimals"}),
        (
            {"accrued_interest_paid": "1.00"},
            {"field": "accrued_interest_paid", "code": "bond_only"},
        ),
        ({"asset_id": None}, {"field": "asset_id", "code": "required"}),
    ],
)
def test_field_rules_reject_with_issues(client: TestClient, setup, changes, issue) -> None:
    body = buy(setup["account"], setup["asset"], "2024-01-10", "10") | changes

    response = create(client, body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_FAILED"
    [entry] = response.json()["transactions"]
    assert entry["index"] == 0 and issue in entry["issues"]
    assert rows(client) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": 10},  # a JSON number, not a decimal string (ADR-0004)
        {"quantity": 10.5},
        {"type": "TRANSFER"},  # not a V1 type
        {"split_ratio": "2/1"},
        {"unknown_field": "x"},
    ],
)
def test_malformed_requests_are_rejected(client: TestClient, setup, changes) -> None:
    body = buy(setup["account"], setup["asset"], "2024-01-10", "10") | changes

    response = create(client, body)

    assert response.status_code == 422 and response.json()["code"] == "REQUEST_INVALID"


def test_dividend_fields_are_a_closed_list(client: TestClient, setup) -> None:
    body = {
        "account_id": setup["account"]["id"], "type": "DIVIDEND", "trade_date": "2024-05-02",
        "asset_id": setup["asset"]["id"], "gross_amount": "20.00", "taxes": "5.20",
        "total_amount": "14.80", "fees": "0",
    }  # fmt: skip

    response = create(client, body)

    assert response.status_code == 422
    assert {"field": "fees", "code": "not_allowed"} in response.json()["transactions"][0]["issues"]


# --- E6: editing (domain-rules §16) -----------------------------------------------------


def test_example_e6_editing(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    jan = created(client, buy(account, asset, "2024-01-15", "10"))
    feb = created(client, sell(account, asset, "2024-02-15", "10"))
    before = rows(client)

    # Delete the BUY -> rejected (the Feb SELL would need 10 units).
    assert_conflict(delete(client, jan["id"]), feb["id"])
    assert rows(client) == before

    # Edit the BUY quantity to 12 -> accepted (2 left).
    twelve = edit(client, jan, quantity="12", gross_amount="1200.00", total_amount="1200.00")
    assert twelve.status_code == 200, twelve.text
    jan = twelve.json()["transactions"][0]
    assert jan["quantity"] == "12" and jan["sequence"] == 1

    # Edit it to 8 -> rejected.
    eight = edit(client, jan, quantity="8", gross_amount="800.00", total_amount="800.00")
    assert_conflict(eight, feb["id"])

    # Move the BUY date after the SELL -> rejected.
    assert_conflict(edit(client, jan, trade_date="2024-03-01"), feb["id"])
    assert client.get(f"/api/v1/transactions/{jan['id']}").json()["quantity"] == "12"

    # Delete the SELL -> accepted.
    assert delete(client, feb["id"]).status_code == 200

    # "Delete all transactions of the asset" -> accepted.
    response = client.delete(
        f"/api/v1/accounts/{account['id']}/assets/{asset['id']}/transactions", headers=CSRF
    )
    assert response.status_code == 200 and response.json()["deleted"] == [jan["id"]]
    assert rows(client) == []


def test_delete_all_for_asset_also_resolves_a_conflicting_history(
    client: TestClient, setup
) -> None:
    account, asset = setup["account"], setup["asset"]
    created(client, buy(account, asset, "2024-01-15", "10"))
    created(client, sell(account, asset, "2024-02-15", "4"))
    created(client, sell(account, asset, "2024-03-15", "6"))
    other = post(client, "/api/v1/assets", ETF | {"name": "Other ETF"})
    kept = created(client, buy(account, other, "2024-01-20", "3"))

    response = client.delete(
        f"/api/v1/accounts/{account['id']}/assets/{asset['id']}/transactions", headers=CSRF
    )

    assert response.status_code == 200 and len(response.json()["deleted"]) == 3
    assert [r[0] for r in rows(client)] == [kept["id"]]


def test_date_edit_keeps_the_insertion_sequence(client: TestClient, setup) -> None:
    deposit = {"account_id": setup["account"]["id"], "type": "DEPOSIT", "total_amount": "100.00"}
    first = created(client, deposit | {"trade_date": "2024-01-02"})
    created(client, deposit | {"trade_date": "2024-01-03"})

    moved = edit(client, first, trade_date="2024-06-30").json()["transactions"][0]

    assert moved["sequence"] == first["sequence"] == 1  # D-70


# --- E7: same-day ordering --------------------------------------------------------------


def test_example_e7_same_day_ordering(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]

    # BUY 10 and SELL 10 on the same date, no time: BUY then SELL -> valid.
    created(client, buy(account, asset, "2024-04-02", "10"))
    created(client, sell(account, asset, "2024-04-02", "10"))

    # Same date, BUY 10 at 10:00 and SELL 10 without time: the untimed SELL goes first.
    timed_buy = created(client, buy(account, asset, "2024-05-02", "10", time="10:00:00"))
    untimed = create(client, sell(account, asset, "2024-05-02", "10"))
    assert untimed.status_code == 409
    assert untimed.json()["conflict"]["index"] == 0  # the new transaction of the request

    # Giving the SELL a later time (11:00) makes it valid.
    created(client, sell(account, asset, "2024-05-02", "10", time="11:00:00"))
    assert len(rows(client)) == 4 and timed_buy["trade_time"] == "10:00:00"


# --- no-deadlock operations (D-06b) -----------------------------------------------------


def test_bulk_edit_fixes_what_no_single_edit_can(client: TestClient, setup) -> None:
    """BUY and SELL recorded on the wrong asset: moving either alone breaks the ledger."""
    account, wrong = setup["account"], setup["asset"]
    right = post(client, "/api/v1/assets", ETF | {"name": "Right ETF"})
    b = created(client, buy(account, wrong, "2024-01-15", "10"))
    s = created(client, sell(account, wrong, "2024-02-15", "10"))

    assert_conflict(edit(client, b, asset_id=right["id"]), s["id"])
    assert edit(client, s, asset_id=right["id"]).status_code == 409

    response = client.post(
        "/api/v1/transactions/bulk-edit",
        headers=CSRF,
        json={"edits": [{"id": b["id"], "asset_id": right["id"]},
                        {"id": s["id"], "asset_id": right["id"]}]},
    )  # fmt: skip

    assert response.status_code == 200, response.text
    assert {t["asset_id"] for t in response.json()["transactions"]} == {right["id"]}
    assert {t["sequence"] for t in response.json()["transactions"]} == {1, 2}


def test_bulk_edit_is_all_or_nothing(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    b = created(client, buy(account, asset, "2024-01-15", "10"))
    s = created(client, sell(account, asset, "2024-02-15", "10"))
    before, audits = rows(client), audit_count(client)

    response = client.post(
        "/api/v1/transactions/bulk-edit",
        headers=CSRF,
        json={"edits": [{"id": b["id"], "notes": "fine"},
                        {"id": s["id"], "quantity": "11", "gross_amount": "1100.00",
                         "total_amount": "1100.00"}]},
    )  # fmt: skip

    assert_conflict(response, s["id"])
    assert rows(client) == before and audit_count(client) == audits


def test_bulk_edit_validation(client: TestClient, setup) -> None:
    b = created(client, buy(setup["account"], setup["asset"], "2024-01-15", "10"))

    twice = client.post(
        "/api/v1/transactions/bulk-edit", headers=CSRF,
        json={"edits": [{"id": b["id"]}, {"id": b["id"]}]},
    )  # fmt: skip
    null_type = client.post(
        "/api/v1/transactions/bulk-edit",
        headers=CSRF,
        json={"edits": [{"id": b["id"], "type": None}]},
    )
    fields = client.post(
        "/api/v1/transactions/bulk-edit", headers=CSRF,
        json={"edits": [{"id": b["id"], "total_amount": "1.00"}]},
    )  # fmt: skip

    assert twice.status_code == 422 and null_type.status_code == 422
    assert fields.status_code == 422
    assert fields.json()["transactions"][0]["transaction_id"] == b["id"]


def test_bulk_delete_removes_dependent_transactions_together(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    b = created(client, buy(account, asset, "2024-01-15", "10"))
    s = created(client, sell(account, asset, "2024-02-15", "10"))

    alone = client.post("/api/v1/transactions/bulk-delete", headers=CSRF, json={"ids": [b["id"]]})
    together = client.post(
        "/api/v1/transactions/bulk-delete", headers=CSRF, json={"ids": [b["id"], s["id"]]}
    )

    assert_conflict(alone, s["id"])
    assert together.status_code == 200 and sorted(together.json()["deleted"]) == sorted(
        [b["id"], s["id"]]
    )
    assert rows(client) == []


# --- moving to another account (M2 plan, answer 3) --------------------------------------


def test_move_a_transaction_to_another_account(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    other = post(client, "/api/v1/accounts", {"name": "Altro", "currency": "EUR"})
    deposit = {"account_id": other["id"], "type": "DEPOSIT", "trade_date": "2024-01-01",
               "total_amount": "5000.00"}  # fmt: skip
    created(client, deposit)
    created(client, deposit)
    b = created(client, buy(account, asset, "2024-01-15", "10"))

    response = edit(client, b, account_id=other["id"])

    assert response.status_code == 200, response.text
    moved = response.json()["transactions"][0]
    assert moved["id"] == b["id"] and moved["account_id"] == other["id"]
    assert moved["sequence"] == 3  # next sequence of the destination account
    assert client.get(f"/api/v1/accounts/{account['id']}").json()["transaction_count"] == 0


def test_a_move_must_keep_both_ledgers_valid(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    other = post(client, "/api/v1/accounts", {"name": "Altro", "currency": "EUR"})
    b = created(client, buy(account, asset, "2024-01-15", "10"))
    s = created(client, sell(account, asset, "2024-02-15", "10"))
    before = rows(client)

    response = edit(client, b, account_id=other["id"])  # the SELL left behind needs it

    assert_conflict(response, s["id"])
    assert response.json()["conflict"]["account_id"] == account["id"]
    assert rows(client) == before


def test_moving_both_with_bulk_edit(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    other = post(client, "/api/v1/accounts", {"name": "Altro", "currency": "EUR"})
    b = created(client, buy(account, asset, "2024-01-15", "10"))
    s = created(client, sell(account, asset, "2024-02-15", "10"))

    response = client.post(
        "/api/v1/transactions/bulk-edit", headers=CSRF,
        json={"edits": [{"id": b["id"], "account_id": other["id"]},
                        {"id": s["id"], "account_id": other["id"]}]},
    )  # fmt: skip

    assert response.status_code == 200, response.text
    seqs = {t["id"]: t["sequence"] for t in response.json()["transactions"]}
    assert sorted(seqs.values()) == [1, 2]


# --- warnings ---------------------------------------------------------------------------


def test_warnings_say_whether_the_change_introduced_them(client: TestClient, setup) -> None:
    account = setup["account"]
    cash = {"account_id": account["id"]}
    created(
        client, cash | {"type": "DEPOSIT", "trade_date": "2024-01-02", "total_amount": "100.00"}
    )

    first = create(
        client, cash | {"type": "WITHDRAWAL", "trade_date": "2024-01-05", "total_amount": "150.00"}
    ).json()
    later = create(
        client, cash | {"type": "DEPOSIT", "trade_date": "2024-02-01", "total_amount": "10.00"}
    ).json()

    assert [(w["code"], w["date"], w["introduced"]) for w in first["warnings"]] == [
        ("NEGATIVE_CASH", "2024-01-05", True)
    ]
    assert [(w["code"], w["date"], w["introduced"]) for w in later["warnings"]] == [
        ("NEGATIVE_CASH", "2024-01-05", False)  # still there, but not caused by this change
    ]


def test_income_on_a_never_held_asset_is_accepted_with_a_warning(client: TestClient, setup) -> None:
    body = {
        "account_id": setup["account"]["id"], "type": "DIVIDEND", "trade_date": "2024-05-02",
        "asset_id": setup["asset"]["id"], "gross_amount": "20.00", "taxes": "5.20",
        "total_amount": "14.80",
    }  # fmt: skip

    response = create(client, body)

    assert response.status_code == 201
    tx_id = response.json()["transactions"][0]["id"]
    assert [
        (w["code"], w["transaction_id"], w["introduced"]) for w in response.json()["warnings"]
    ] == [("INCOME_WITHOUT_HOLDING", tx_id, True)]


# --- list -------------------------------------------------------------------------------


def test_list_filters_and_pagination(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    created(client, {"account_id": account["id"], "type": "DEPOSIT", "trade_date": "2024-01-02",
                     "total_amount": "2000.00"})  # fmt: skip
    b = created(client, buy(account, asset, "2024-02-01", "10"))
    s = created(client, sell(account, asset, "2024-03-01", "5"))

    page = client.get("/api/v1/transactions", params={"limit": 2}).json()
    assert page["total"] == 3 and [t["id"] for t in page["items"]] == [s["id"], b["id"]]
    by_type = client.get("/api/v1/transactions", params={"type": ["BUY", "SELL"]}).json()
    assert by_type["total"] == 2
    by_asset = client.get("/api/v1/transactions", params={"asset_id": asset["id"]}).json()
    assert by_asset["total"] == 2
    by_date = client.get(
        "/api/v1/transactions", params={"date_from": "2024-02-01", "date_to": "2024-02-28"}
    ).json()
    assert [t["id"] for t in by_date["items"]] == [b["id"]]
    assert client.get("/api/v1/transactions", params={"limit": 501}).status_code == 422


# --- audit ------------------------------------------------------------------------------


def test_changes_are_audited_with_before_and_after(client: TestClient, setup) -> None:
    b = created(client, buy(setup["account"], setup["asset"], "2024-01-15", "10"))
    edit(client, b, notes="checked")
    delete(client, b["id"])

    with Session(client.app.state.engine) as db:
        entries = db.scalars(
            select(AuditEntry).where(AuditEntry.entity_id == b["id"]).order_by(AuditEntry.at)
        ).all()
    assert [e.action for e in entries] == [
        "transaction.created", "transaction.updated", "transaction.deleted",
    ]  # fmt: skip
    assert entries[0].after["quantity"] == "10" and entries[0].after["gross_amount"] == "1000.00"
    assert entries[1].before["notes"] is None and entries[1].after["notes"] == "checked"
    assert entries[2].before["sequence"] == 1


# --- owner isolation --------------------------------------------------------------------


def test_another_user_cannot_touch_my_transactions(client: TestClient, setup) -> None:
    account, asset = setup["account"], setup["asset"]
    mine = created(client, buy(account, asset, "2024-01-15", "10"))
    bob = investor(client, "bob")
    bobs_account = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, bob)
    before = rows(client)

    assert client.get("/api/v1/transactions", headers=bob).json() == {"items": [], "total": 0}
    other_filter = client.get(
        "/api/v1/transactions", headers=bob, params={"account_id": account["id"]}
    )
    assert other_filter.json()["total"] == 0
    attempts = [
        client.get(f"/api/v1/transactions/{mine['id']}", headers=bob),
        client.put(f"/api/v1/transactions/{mine['id']}", headers=bob,
                   json=buy(bobs_account, asset, "2024-01-15", "10")),
        client.delete(f"/api/v1/transactions/{mine['id']}", headers=bob),
        client.post("/api/v1/transactions/bulk-edit", headers=bob,
                    json={"edits": [{"id": mine["id"], "notes": "x"}]}),
        client.post("/api/v1/transactions/bulk-delete", headers=bob, json={"ids": [mine["id"]]}),
        create(client, buy(account, asset, "2024-01-16", "1"), headers=bob),  # my account
        create(client, buy(bobs_account, asset, "2024-01-16", "1"), headers=bob),  # my asset
        client.delete(f"/api/v1/accounts/{account['id']}/assets/{asset['id']}/transactions",
                      headers=bob),
    ]  # fmt: skip

    for response in attempts:
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "NOT_FOUND"
    assert rows(client) == before


def test_bulk_requests_mixing_my_and_foreign_ids_change_nothing(client: TestClient, setup) -> None:
    mine = created(client, buy(setup["account"], setup["asset"], "2024-01-15", "10"))
    bob = investor(client, "bob")
    bobs_account = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, bob)
    deposit = {"account_id": bobs_account["id"], "type": "DEPOSIT", "trade_date": "2024-01-02",
               "total_amount": "1.00"}  # fmt: skip
    bobs = created(client, deposit, headers=bob)
    before = rows(client)

    response = client.post(
        "/api/v1/transactions/bulk-delete", headers=bob, json={"ids": [bobs["id"], mine["id"]]}
    )

    assert response.status_code == 404 and rows(client) == before


def test_moving_into_another_users_account_is_404(client: TestClient, setup) -> None:
    mine = created(client, buy(setup["account"], setup["asset"], "2024-01-15", "10"))
    bob = investor(client, "bob")
    bobs_account = post(client, "/api/v1/accounts", {"name": "Bob", "currency": "EUR"}, bob)

    response = edit(client, mine, account_id=bobs_account["id"])

    assert response.status_code == 404
    assert (
        client.get(f"/api/v1/transactions/{mine['id']}").json()["account_id"]
        == setup["account"]["id"]
    )
