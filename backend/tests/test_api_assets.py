"""Assets (private/shared visibility, D-35, D-37) and manual prices. Invented data only."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, fake_isin, investor
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portfolio_tracker.db.models import Asset, AuditEntry, Instrument, Price, Transaction
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


def test_manual_price_changes_are_audited(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)
    url = f"/api/v1/assets/{asset['id']}/prices"
    first = client.put(url, headers=CSRF, json={"day": "2024-05-02", "close": "101.50"}).json()
    client.put(url, headers=CSRF, json={"day": "2024-05-02", "close": "102.00"})
    client.delete(f"{url}/{first['id']}", headers=CSRF)

    with Session(client.app.state.engine) as db:
        entries = db.scalars(
            select(AuditEntry).where(AuditEntry.entity_id == first["id"]).order_by(AuditEntry.at)
        ).all()
    assert [e.action for e in entries] == ["price.set", "price.set", "price.deleted"]
    assert entries[0].before is None and entries[0].after["close"] == "101.50"
    assert entries[1].before["close"] == "101.50" and entries[1].after["close"] == "102.00"
    assert entries[2].before == {"asset_id": asset["id"], "day": "2024-05-02", "close": "102.00"}


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


# The test clock reads 2026-01-15 09:00 UTC: tomorrow is the 16th.
@pytest.mark.parametrize("day", ["2026-01-15", "2026-01-16", "1990-01-02"])
def test_a_manual_price_may_be_dated_up_to_tomorrow(client: TestClient, day: str) -> None:
    create_admin(client)
    asset = new_asset(client)

    response = client.put(
        f"/api/v1/assets/{asset['id']}/prices", headers=CSRF, json={"day": day, "close": "10"}
    )

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(("now", "day"), [((9, 0), "2026-01-17"), ((23, 59), "2026-01-17")])
def test_a_day_after_tomorrow_is_refused_and_nothing_is_written(
    client: TestClient, clock, now: tuple[int, int], day: str
) -> None:
    """D-149: the server's date decides, whatever the hour."""
    create_admin(client)
    asset = new_asset(client)
    clock.now = clock.now.replace(hour=now[0], minute=now[1])

    response = client.put(
        f"/api/v1/assets/{asset['id']}/prices", headers=CSRF, json={"day": day, "close": "10"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert response.json()["issues"] == [{"field": "day", "code": "future_date"}]
    with Session(client.app.state.engine) as db:
        assert db.query(Price).count() == 0
        assert db.scalars(select(AuditEntry).where(AuditEntry.action == "price.set")).all() == []


def test_a_future_day_replaces_no_existing_price(client: TestClient) -> None:
    create_admin(client)
    asset = new_asset(client)
    url = f"/api/v1/assets/{asset['id']}/prices"
    client.put(url, headers=CSRF, json={"day": "2026-01-16", "close": "10"})

    refused = client.put(url, headers=CSRF, json={"day": "2026-01-20", "close": "99"})

    assert refused.status_code == 422
    assert [(p["day"], p["close"]) for p in client.get(url).json()] == [("2026-01-16", "10")]


def test_another_users_security_is_404_before_the_day_is_checked(client: TestClient) -> None:
    """Ownership first (D-92): a refused day must not confirm that the security exists, for
    another investor or for the Administrator."""
    create_admin(client)
    alice, bob = investor(client, "alice"), investor(client, "bob")
    mine = new_asset(client, alice | CSRF)
    url = f"/api/v1/assets/{mine['id']}/prices"
    price = client.put(url, headers=alice, json={"day": "2026-01-02", "close": "5"}).json()

    for headers in (bob, CSRF):  # CSRF alone: the Administrator's own session cookie
        for method, path, body in [
            ("GET", url, None),
            ("PUT", url, {"day": "2026-01-02", "close": "1"}),
            ("PUT", url, {"day": "2026-02-01", "close": "1"}),
            ("DELETE", f"{url}/{price['id']}", None),
        ]:
            response = client.request(method, path, headers=headers, json=body)
            assert response.status_code == 404, (headers is bob, method, body)
    assert [p["close"] for p in client.get(url, headers=alice).json()] == ["5"]


# --- one private asset per ISIN and user ------------------------------------------------


def test_a_second_private_asset_with_the_same_isin_is_rejected(client: TestClient) -> None:
    create_admin(client)
    isin = fake_isin(20)
    new_asset(client, isin=isin)

    again = client.post("/api/v1/assets", headers=CSRF, json=ETF | {"isin": isin.lower()})

    assert again.status_code == 409 and again.json()["code"] == "ISIN_ALREADY_USED"
    assert len(client.get("/api/v1/assets").json()) == 1


def test_the_isin_stays_optional(client: TestClient) -> None:
    create_admin(client)

    new_asset(client)
    new_asset(client, name="Another asset without ISIN")

    assert len(client.get("/api/v1/assets").json()) == 2


def test_editing_to_an_isin_already_used_is_rejected(client: TestClient) -> None:
    create_admin(client)
    isin = fake_isin(21)
    new_asset(client, isin=isin)
    other = new_asset(client, name="Other")

    taken = client.patch(f"/api/v1/assets/{other['id']}", headers=CSRF, json={"isin": isin})
    same = client.patch(f"/api/v1/assets/{other['id']}", headers=CSRF, json={"isin": fake_isin(22)})
    keep = client.patch(f"/api/v1/assets/{other['id']}", headers=CSRF, json={"isin": fake_isin(22)})

    assert taken.status_code == 409 and taken.json()["code"] == "ISIN_ALREADY_USED"
    assert same.status_code == 200 and keep.status_code == 200  # its own ISIN again is fine


@pytest.mark.parametrize(
    "change", [{"isin": fake_isin(24)}, {"isin": None}, {"quote_currency": "USD"}]
)
def test_changing_the_isin_or_currency_unlinks_the_instrument(
    client: TestClient, change: dict
) -> None:
    """Another ISIN or currency is another instrument (D-116, D-125): the old link must go,
    so the asset is linked again (or not at all) by the market data service."""
    create_admin(client)
    asset = new_asset(client, isin=fake_isin(23))
    with Session(client.app.state.engine) as db, db.begin():
        instrument = Instrument(isin=fake_isin(23), quote_currency="EUR")
        db.add(instrument)
        db.flush()
        db.get(Asset, UUID(asset["id"])).instrument_id = instrument.id

    renamed = client.patch(f"/api/v1/assets/{asset['id']}", headers=CSRF, json={"name": "New"})
    with Session(client.app.state.engine) as db:
        assert db.get(Asset, UUID(asset["id"])).instrument_id is not None  # a name is not
    changed = client.patch(f"/api/v1/assets/{asset['id']}", headers=CSRF, json=change)

    assert renamed.status_code == 200 and changed.status_code == 200, changed.text
    with Session(client.app.state.engine) as db:
        assert db.get(Asset, UUID(asset["id"])).instrument_id is None


def test_the_same_isin_is_allowed_for_different_users_and_a_shared_asset(
    client: TestClient,
) -> None:
    create_admin(client)
    alice, bob = investor(client, "alice"), investor(client, "bob")
    isin = fake_isin(23)
    with Session(client.app.state.engine) as db, db.begin():
        db.add(Asset(name="Shared", asset_type="ETF", quote_currency="EUR",
                     quote_unit="CURRENCY", isin=isin))  # fmt: skip

    new_asset(client, alice | CSRF, isin=isin)
    new_asset(client, bob | CSRF, isin=isin)  # D-35: one private copy per user


def test_the_database_also_enforces_one_private_isin_per_user(client: TestClient) -> None:
    create_admin(client)
    isin = fake_isin(24)
    owner = new_asset(client, isin=isin)

    with pytest.raises(IntegrityError), Session(client.app.state.engine) as db, db.begin():
        stored = db.get(Asset, UUID(owner["id"]))
        db.add(Asset(owner_id=stored.owner_id, name="Copy", asset_type="ETF",
                     quote_currency="EUR", quote_unit="CURRENCY", isin=isin))  # fmt: skip
