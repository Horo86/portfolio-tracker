"""Accounts: CRUD, currency lock, typed-name delete, owner isolation. Invented data only."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, investor
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import AuditEntry, Transaction
from portfolio_tracker.repositories import transactions as txs


def new_account(client: TestClient, headers=None, **fields) -> dict:
    body = {"name": "Conto titoli", "currency": "EUR", "provider": "Example Broker"} | fields
    response = client.post("/api/v1/accounts", headers=headers or CSRF, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_deposit(client: TestClient, account_id: str) -> None:
    """A transaction inserted directly (the transactions API arrives in step 9)."""
    with Session(client.app.state.engine) as db, db.begin():
        db.add(
            Transaction(
                account_id=UUID(account_id),
                type="DEPOSIT",
                trade_date=date(2024, 1, 2),
                sequence=txs.next_sequence(db, UUID(account_id)),
                total_amount=Decimal("1000.00"),
            )
        )


def transaction_count(client: TestClient) -> int:
    with Session(client.app.state.engine) as db:
        return db.scalar(select(func.count()).select_from(Transaction))


def last_audit(client: TestClient) -> AuditEntry:
    with Session(client.app.state.engine) as db:
        return db.scalars(select(AuditEntry).order_by(AuditEntry.at.desc())).first()


# --- create, read, list -----------------------------------------------------------------


def test_create_and_read_an_account(client: TestClient) -> None:
    create_admin(client)

    created = new_account(client, external_code=" 12345 ")

    assert created["name"] == "Conto titoli"
    assert created["currency"] == "EUR"
    assert created["provider"] == "Example Broker"
    assert created["external_code"] == "12345"
    assert created["transaction_count"] == 0 and created["currency_locked"] is False
    assert client.get(f"/api/v1/accounts/{created['id']}").json() == created
    assert last_audit(client).action == "account.created"


def test_list_accounts_in_creation_order(client: TestClient, clock) -> None:
    create_admin(client)
    first = new_account(client, name="Conto titoli")
    clock.advance(seconds=1)
    second = new_account(client, name="Conto estero", currency="USD", provider=None)

    listed = client.get("/api/v1/accounts").json()

    assert [a["id"] for a in listed] == [first["id"], second["id"]]
    assert listed[1]["provider"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "currency": "EUR"},
        {"name": "   ", "currency": "EUR"},
        {"name": "x" * 101, "currency": "EUR"},
        {"name": "Conto", "currency": "eur"},
        {"name": "Conto", "currency": "ABC"},
        {"name": "Conto"},
        {"name": "Conto", "currency": "EUR", "owner_id": str(uuid4())},
    ],
)
def test_create_account_validation(client: TestClient, body: dict) -> None:
    create_admin(client)

    response = client.post("/api/v1/accounts", headers=CSRF, json=body)

    assert response.status_code == 422
    assert client.get("/api/v1/accounts").json() == []


# --- update and currency lock -----------------------------------------------------------


def test_rename_and_change_provider(client: TestClient) -> None:
    create_admin(client)
    account = new_account(client)

    response = client.patch(
        f"/api/v1/accounts/{account['id']}",
        headers=CSRF,
        json={"name": "Conto principale", "provider": ""},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Conto principale"
    assert response.json()["provider"] is None  # empty clears it
    entry = last_audit(client)
    assert entry.action == "account.updated"
    assert entry.before["name"] == "Conto titoli" and entry.after["name"] == "Conto principale"


def test_currency_can_change_while_there_are_no_transactions(client: TestClient) -> None:
    create_admin(client)
    account = new_account(client)

    response = client.patch(
        f"/api/v1/accounts/{account['id']}", headers=CSRF, json={"currency": "USD"}
    )

    assert response.status_code == 200 and response.json()["currency"] == "USD"


def test_currency_is_locked_once_the_account_has_a_transaction(client: TestClient) -> None:
    create_admin(client)
    account = new_account(client)
    add_deposit(client, account["id"])

    shown = client.get(f"/api/v1/accounts/{account['id']}").json()
    assert shown["currency_locked"] is True and shown["transaction_count"] == 1
    locked = client.patch(
        f"/api/v1/accounts/{account['id']}", headers=CSRF, json={"currency": "USD"}
    )
    assert locked.status_code == 409 and locked.json()["code"] == "CURRENCY_LOCKED"
    same = client.patch(
        f"/api/v1/accounts/{account['id']}", headers=CSRF, json={"currency": "EUR", "name": "X"}
    )
    assert same.status_code == 200  # re-sending the same currency is not a change


@pytest.mark.parametrize("body", [{"name": None}, {"currency": None}, {"currency": "XYZ"}])
def test_update_validation(client: TestClient, body: dict) -> None:
    create_admin(client)
    account = new_account(client)

    response = client.patch(f"/api/v1/accounts/{account['id']}", headers=CSRF, json=body)

    assert response.status_code == 422


# --- delete -----------------------------------------------------------------------------


def test_delete_needs_the_typed_name(client: TestClient) -> None:
    create_admin(client)
    account = new_account(client, name="Conto estero")
    add_deposit(client, account["id"])

    wrong = client.request(
        "DELETE", f"/api/v1/accounts/{account['id']}", headers=CSRF,
        json={"confirm_name": "conto estero"},
    )  # fmt: skip
    assert wrong.status_code == 422 and wrong.json()["code"] == "CONFIRMATION_MISMATCH"
    missing = client.request("DELETE", f"/api/v1/accounts/{account['id']}", headers=CSRF)
    assert missing.status_code == 422
    assert transaction_count(client) == 1


def test_delete_removes_the_account_and_its_transactions(client: TestClient) -> None:
    create_admin(client)
    account = new_account(client, name="Conto estero")
    other = new_account(client, name="Conto titoli")
    add_deposit(client, account["id"])
    add_deposit(client, account["id"])
    add_deposit(client, other["id"])

    response = client.request(
        "DELETE", f"/api/v1/accounts/{account['id']}", headers=CSRF,
        json={"confirm_name": " Conto estero "},
    )  # fmt: skip

    assert response.status_code == 200 and response.json() == {"deleted_transactions": 2}
    assert client.get(f"/api/v1/accounts/{account['id']}").status_code == 404
    assert transaction_count(client) == 1  # the other account is untouched
    entry = last_audit(client)
    assert entry.action == "account.deleted"
    assert entry.before["transaction_count"] == 2


# --- owner isolation --------------------------------------------------------------------


def test_accounts_of_another_user_are_invisible(client: TestClient) -> None:
    create_admin(client)
    alice = investor(client, "alice")
    bob = investor(client, "bob")
    mine = new_account(client, alice | CSRF, name="Alice account")

    assert client.get("/api/v1/accounts", headers=bob).json() == []
    path = f"/api/v1/accounts/{mine['id']}"
    for method, body in [
        ("GET", None),
        ("PATCH", {"name": "stolen"}),
        ("DELETE", {"confirm_name": "Alice account"}),
    ]:
        response = client.request(method, path, headers=bob, json=body)
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert client.get(path, headers=alice).json()["name"] == "Alice account"


def test_administrator_cannot_see_other_users_accounts(client: TestClient) -> None:
    create_admin(client)  # the client holds the admin cookie
    alice = investor(client, "alice")
    mine = new_account(client, alice | CSRF)

    assert client.get("/api/v1/accounts").json() == []
    assert client.get(f"/api/v1/accounts/{mine['id']}").status_code == 404


def test_unknown_account_is_404(client: TestClient) -> None:
    create_admin(client)

    assert client.get(f"/api/v1/accounts/{uuid4()}").status_code == 404


def test_accounts_need_a_session(client: TestClient) -> None:
    assert client.get("/api/v1/accounts").status_code == 401
    assert client.post("/api/v1/accounts", json={"name": "A", "currency": "EUR"}).status_code == 401
