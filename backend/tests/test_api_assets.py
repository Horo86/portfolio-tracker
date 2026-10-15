"""Assets (private/shared visibility, D-35, D-37) and manual prices. Invented data only."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Asset, Price, Transaction
from portfolio_tracker.repositories import accounts
from portfolio_tracker.repositories import transactions as txs

ETF = {
    "name": "Example World Equity ETF",
    "asset_type": "ETF",
    "quote_currency": "EUR",
    "quote_unit": "CURRENCY",
}
BOND = {
    "name": "Example Government Bond 2030",
    "asset_type": "BOND",
    "quote_currency": "EUR",
    "quote_unit": "PERCENT_OF_NOMINAL",
}


def new_asset(client: TestClient, headers=None, **fields) -> dict:
    response = client.post("/api/v1/assets", headers=headers or CSRF, json=ETF | fields)
    assert response.status_code == 201, response.text
    return response.json()


def shared_asset(client: TestClient, name: str = "Shared Example Stock") -> str:
    """A provider-resolved asset (created by providers in M4; inserted directly here)."""
    with Session(client.app.state.engine) as db, db.begin():
        asset = Asset(name=name, asset_type="STOCK", quote_currency="USD", quote_unit="CURRENCY")
        db.add(asset)
        db.flush()
        return str(asset.id)


def use_asset(client: TestClient, username: str, asset_id: str) -> None:
    """A BUY of the asset in a new account of `username` (transactions API: step 9)."""
    with Session(client.app.state.engine) as db, db.begin():
        from portfolio_tracker.repositories import users

        owner = users.get_by_username(db, username)
        account = accounts.add_account(db, owner.id, name="Holder", currency="EUR")
        db.add(
            Transaction(
                account_id=account.id,
                asset_id=UUID(asset_id),
                type="BUY",
                trade_date=date(2024, 1, 2),
                sequence=txs.next_sequence(db, account.id),
                quantity=Decimal("10"),
                gross_amount=Decimal("1000.00"),
                total_amount=Decimal("1000.00"),
            )
        )


# --- private assets ---------------------------------------------------------------------


def test_create_a_private_asset(client: TestClient) -> None:
    create_admin(client)
    isin = fake_isin(1)

    created = new_asset(client, isin=isin.lower(), ticker="EXW")

    assert created["isin"] == isin  # normalised to upper case
    assert created["shared"] is False and created["in_use"] is False
    assert client.get("/api/v1/assets").json() == [created]
    assert client.get(f"/api/v1/assets/{created['id']}").json() == created


def test_bonds_and_only_bonds_use_percent_of_nominal(client: TestClient) -> None:
    create_admin(client)
    assert new_asset(client, **BOND)["quote_unit"] == "PERCENT_OF_NOMINAL"

    for body in (BOND | {"quote_unit": "CURRENCY"}, ETF | {"quote_unit": "PERCENT_OF_NOMINAL"}):
        response = client.post("/api/v1/assets", headers=CSRF, json=body)
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert response.json()["issues"] == [
            {"field": "quote_unit", "code": "must_match_asset_type"}
        ]


def test_minor_currency_unit_asset(client: TestClient) -> None:
    create_admin(client)

    created = new_asset(
        client, asset_type="STOCK", quote_currency="GBP", quote_unit="MINOR_CURRENCY_UNIT"
    )

    assert created["quote_unit"] == "MINOR_CURRENCY_UNIT"


@pytest.mark.parametrize(
    "fields",
    [
        {"isin": "ZZ0000000001"},  # wrong check digit
        {"isin": "ZZ00000001"},
        {"asset_type": "FUND"},
        {"asset_type": "CRYPTO"},  # out of scope (D-02)
        {"quote_currency": "GBX"},  # pence are a quote unit, not a currency (D-44)
        {"name": ""},
        {"owner_id": None},
    ],
)
def test_create_asset_validation(client: TestClient, fields: dict) -> None:
    create_admin(client)

    response = client.post("/api/v1/assets", headers=CSRF, json=ETF | fields)

    assert response.status_code == 422
    assert client.get("/api/v1/assets").json() == []


def test_edit_a_private_asset(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client, ticker="EXW")

    response = client.patch(
        f"/api/v1/assets/{asset['id']}",
        headers=CSRF,
        json={"name": "Renamed ETF", "ticker": "", "quote_currency": "USD"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["ticker"], body["quote_currency"]) == ("Renamed ETF", None, "USD")


def test_edit_that_breaks_the_bond_rule_is_rejected(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)

    response = client.patch(
        f"/api/v1/assets/{asset['id']}", headers=CSRF, json={"asset_type": "BOND"}
    )

    assert response.status_code == 422
    assert client.get(f"/api/v1/assets/{asset['id']}").json()["asset_type"] == "ETF"


def test_structural_fields_are_locked_while_transactions_use_the_asset(
    client: TestClient,
) -> None:
    create_admin(client)
    asset = new_asset(client, isin=fake_isin(2))
    use_asset(client, "admin", asset["id"])

    assert client.get(f"/api/v1/assets/{asset['id']}").json()["in_use"] is True
    for change in (
        {"quote_currency": "USD"},
        {"asset_type": "STOCK"},
        {"isin": fake_isin(3)},
        {"isin": None},
    ):
        response = client.patch(f"/api/v1/assets/{asset['id']}", headers=CSRF, json=change)
        assert response.status_code == 409 and response.json()["code"] == "ASSET_IN_USE"
    renamed = client.patch(
        f"/api/v1/assets/{asset['id']}",
        headers=CSRF,
        json={"name": "New name", "ticker": "NEW", "quote_currency": "EUR"},  # same currency
    )
    assert renamed.status_code == 200


def test_delete_an_unused_asset_with_its_manual_prices(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)
    client.put(
        f"/api/v1/assets/{asset['id']}/prices",
        headers=CSRF,
        json={"day": "2024-05-02", "close": "101.50"},
    )

    assert client.delete(f"/api/v1/assets/{asset['id']}", headers=CSRF).status_code == 204
    assert client.get(f"/api/v1/assets/{asset['id']}").status_code == 404
    with Session(client.app.state.engine) as db:
        assert db.query(Price).count() == 0


def test_an_asset_in_use_cannot_be_deleted(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)
    use_asset(client, "admin", asset["id"])

    response = client.delete(f"/api/v1/assets/{asset['id']}", headers=CSRF)

    assert response.status_code == 409 and response.json()["code"] == "ASSET_IN_USE"


# --- visibility (D-35) ------------------------------------------------------------------


def test_private_assets_are_invisible_to_other_users(client: TestClient) -> None:
    create_admin(client)
    alice, bob = investor(client, "alice"), investor(client, "bob")
    mine = new_asset(client, alice | CSRF, isin=fake_isin(4))

    assert client.get("/api/v1/assets", headers=bob).json() == []
    path = f"/api/v1/assets/{mine['id']}"
    for method, url, body in [
        ("GET", path, None),
        ("PATCH", path, {"name": "stolen"}),
        ("DELETE", path, None),
        ("GET", f"{path}/prices", None),
        ("PUT", f"{path}/prices", {"day": "2024-01-02", "close": "1"}),
    ]:
        response = client.request(method, url, headers=bob, json=body)
        assert response.status_code == 404, (method, url)
    assert client.get("/api/v1/assets").json() == []  # nor to the administrator
    assert client.get(path, headers=alice).json()["name"] == ETF["name"]


def test_shared_asset_is_visible_only_to_its_holders_and_read_only(client: TestClient) -> None:
    create_admin(client)
    alice, bob = investor(client, "alice"), investor(client, "bob")
    shared = shared_asset(client)
    use_asset(client, "alice", shared)

    listed = client.get("/api/v1/assets", headers=alice).json()
    assert [a["id"] for a in listed] == [shared] and listed[0]["shared"] is True
    # Bob must not learn that someone holds it.
    assert client.get("/api/v1/assets", headers=bob).json() == []
    assert client.get(f"/api/v1/assets/{shared}", headers=bob).status_code == 404
    # Its holder cannot change shared reference data.
    edit = client.patch(f"/api/v1/assets/{shared}", headers=alice, json={"name": "mine"})
    assert edit.status_code == 403 and edit.json()["code"] == "ASSET_READ_ONLY"
    delete = client.delete(f"/api/v1/assets/{shared}", headers=alice)
    assert delete.status_code == 403


# --- manual prices ----------------------------------------------------------------------


def test_set_list_replace_and_delete_manual_prices(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)
    url = f"/api/v1/assets/{asset['id']}/prices"

    first = client.put(url, headers=CSRF, json={"day": "2024-05-02", "close": "101.50"}).json()
    client.put(url, headers=CSRF, json={"day": "2024-05-03", "close": "0.00000001"})
    replaced = client.put(url, headers=CSRF, json={"day": "2024-05-02", "close": "102.00"}).json()

    assert replaced["id"] == first["id"] and replaced["close"] == "102.00"
    listed = client.get(url).json()
    assert [(p["day"], p["close"], p["source"]) for p in listed] == [
        ("2024-05-03", "0.00000001", "MANUAL"),
        ("2024-05-02", "102.00", "MANUAL"),
    ]
    assert client.delete(f"{url}/{first['id']}", headers=CSRF).status_code == 204
    assert len(client.get(url).json()) == 1
    assert client.delete(f"{url}/{first['id']}", headers=CSRF).status_code == 404


@pytest.mark.parametrize("close", [101.5, "1e3", "-1", "abc", ""])
def test_price_must_be_a_non_negative_decimal_string(client: TestClient, close) -> None:
    create_admin(client)
    asset = new_asset(client)

    response = client.put(
        f"/api/v1/assets/{asset['id']}/prices",
        headers=CSRF,
        json={"day": "2024-05-02", "close": close},
    )

    assert response.status_code == 422


def test_manual_prices_are_per_user_on_a_shared_asset(client: TestClient) -> None:
    create_admin(client)
    alice, bob = investor(client, "alice"), investor(client, "bob")
    shared = shared_asset(client)
    use_asset(client, "alice", shared)
    use_asset(client, "bob", shared)
    url = f"/api/v1/assets/{shared}/prices"

    alices = client.put(url, headers=alice, json={"day": "2024-05-02", "close": "10"}).json()
    client.put(url, headers=bob, json={"day": "2024-05-02", "close": "99"})

    assert [p["close"] for p in client.get(url, headers=alice).json()] == ["10"]
    assert [p["close"] for p in client.get(url, headers=bob).json()] == ["99"]
    stolen = client.delete(f"{url}/{alices['id']}", headers=bob)
    assert stolen.status_code == 404
    assert len(client.get(url, headers=alice).json()) == 1


def test_price_of_another_asset_cannot_be_deleted_through_this_one(client: TestClient) -> None:
    create_admin(client)
    first, second = new_asset(client), new_asset(client, name="Second ETF")
    price = client.put(
        f"/api/v1/assets/{first['id']}/prices",
        headers=CSRF,
        json={"day": "2024-05-02", "close": "1"},
    ).json()

    response = client.delete(f"/api/v1/assets/{second['id']}/prices/{price['id']}", headers=CSRF)

    assert response.status_code == 404


def test_unknown_asset_is_404(client: TestClient) -> None:
    create_admin(client)

    assert client.get(f"/api/v1/assets/{uuid4()}").status_code == 404
    assert client.get(f"/api/v1/assets/{uuid4()}/prices").status_code == 404
