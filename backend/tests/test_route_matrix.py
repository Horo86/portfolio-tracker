"""Authentication and owner-isolation matrix over every API route (M2 "Done when").

Every route of the app must be listed in ROUTES, or `test_every_route_is_in_the_matrix`
fails: a new endpoint cannot ship without saying who may call it. For each route:
- without a session → 401 NOT_AUTHENTICATED (except the public ones);
- OWNED: another investor and the Administrator, using the owner's ids → 404, and nothing
  changes (D-33: no API path for the Administrator to other users' data);
- ADMIN: an investor → 404 (never 403, UI README);
- SELF: acts only on the caller; its GET responses never contain another user's ids.
Invented data only.
"""

import base64
from collections.abc import Callable
from enum import Enum
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import (
    PASSWORD,
    bearer,
    create_admin,
    details,
    enable_two_factor,
    fake_isin,
    investor,
    login_token,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

import portfolio_tracker
from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app


class Access(Enum):
    PUBLIC = "public"  # no session needed
    SELF = "self"  # needs a session; acts on the caller only, takes no other user's ids
    OWNED = "owned"  # takes ids of owned data (path or body): only the owner may use them
    ADMIN = "admin"  # Administrator only


Ids = dict[str, str]
Body = Callable[[Ids], dict | None] | None

TX_FIELDS = {"type": "BUY", "trade_date": "2025-01-10", "quantity": "1", "unit_price": "10.00",
             "gross_amount": "10.00", "total_amount": "10.00"}  # fmt: skip


def _tx(ids: Ids) -> dict:
    return TX_FIELDS | {"account_id": ids["account_id"], "asset_id": ids["asset_id"]}


IMPORT_CSV = base64.b64encode(
    b"account,trade_date,type,total_amount\nMain,2025-01-02,DEPOSIT,5.00\n"
).decode()


def _import(ids: Ids) -> dict:
    """An app CSV mapped onto the owner's account: only the owner may use that account."""
    return {"content_base64": IMPORT_CSV, "accounts": {"Main": ids["account_id"]}}


def _confirm(ids: Ids) -> dict:
    return _import(ids) | {"fingerprint": "0" * 64, "selected": ["line:2"]}


A = Access
V1 = "/api/v1"
ROUTES: dict[tuple[str, str], tuple[Access, Body]] = {
    ("GET", "/api/health"): (A.PUBLIC, None),
    ("GET", f"{V1}/setup/status"): (A.PUBLIC, None),
    ("POST", f"{V1}/setup/admin"): (
        A.PUBLIC,
        lambda _: {"username": "x" * 3, "password": PASSWORD},
    ),
    ("POST", f"{V1}/setup/restore"): (A.PUBLIC, None),
    ("POST", f"{V1}/auth/login"): (A.PUBLIC, lambda _: {"username": "nobody", "password": "x"}),
    ("POST", f"{V1}/auth/login/two-factor"): (A.PUBLIC, lambda _: {"code": "000000"}),
    ("POST", f"{V1}/auth/logout"): (A.SELF, None),
    ("POST", f"{V1}/auth/logout-others"): (A.SELF, None),
    ("GET", f"{V1}/me"): (A.SELF, None),
    ("PATCH", f"{V1}/me"): (A.SELF, lambda _: {"theme": "ambra"}),
    ("POST", f"{V1}/me/password"): (
        A.SELF,
        lambda _: {"current_password": "x" * 12, "new_password": "y" * 12},
    ),
    ("GET", f"{V1}/me/sessions"): (A.SELF, None),
    ("POST", f"{V1}/me/two-factor/setup"): (A.SELF, lambda _: {"password": "x" * 12}),
    ("POST", f"{V1}/me/two-factor/confirm"): (A.SELF, lambda _: {"code": "000000"}),
    ("POST", f"{V1}/me/two-factor/disable"): (
        A.SELF,
        lambda _: {"password": "x" * 12, "code": "000000"},
    ),
    ("POST", f"{V1}/me/two-factor/recovery-codes"): (
        A.SELF,
        lambda _: {"password": "x" * 12, "code": "000000"},
    ),
    ("POST", f"{V1}/me/two-factor/notice/dismiss"): (A.SELF, None),
    ("GET", f"{V1}/admin/system"): (A.ADMIN, None),
    ("GET", f"{V1}/admin/users"): (A.ADMIN, None),
    ("POST", f"{V1}/admin/users"): (A.ADMIN, lambda _: {"username": "carol", **details("carol")}),
    ("POST", f"{V1}/admin/users/{{user_id}}/reset-password"): (A.ADMIN, None),
    ("POST", f"{V1}/admin/users/{{user_id}}/disable"): (A.ADMIN, None),
    ("POST", f"{V1}/admin/users/{{user_id}}/enable"): (A.ADMIN, None),
    ("POST", f"{V1}/admin/market-data/run"): (A.ADMIN, None),
    ("GET", f"{V1}/admin/market-data/status"): (A.ADMIN, None),
    ("GET", f"{V1}/accounts"): (A.SELF, None),
    ("POST", f"{V1}/accounts"): (A.SELF, lambda _: {"name": "New", "currency": "EUR"}),
    ("GET", f"{V1}/accounts/{{account_id}}"): (A.OWNED, None),
    ("PATCH", f"{V1}/accounts/{{account_id}}"): (A.OWNED, lambda _: {"name": "Taken"}),
    ("DELETE", f"{V1}/accounts/{{account_id}}"): (A.OWNED, lambda _: {"confirm_name": "Main"}),
    ("GET", f"{V1}/assets"): (A.SELF, None),
    ("POST", f"{V1}/assets"): (
        A.SELF,
        lambda _: {
            "name": "N",
            "asset_type": "ETF",
            "quote_currency": "EUR",
            "quote_unit": "CURRENCY",
        },
    ),
    ("POST", f"{V1}/assets/search"): (A.SELF, lambda _: {"q": "Example"}),
    ("GET", f"{V1}/assets/{{asset_id}}"): (A.OWNED, None),
    ("PATCH", f"{V1}/assets/{{asset_id}}"): (A.OWNED, lambda _: {"name": "Taken"}),
    ("DELETE", f"{V1}/assets/{{asset_id}}"): (A.OWNED, None),
    ("GET", f"{V1}/assets/{{asset_id}}/prices"): (A.OWNED, None),
    ("PUT", f"{V1}/assets/{{asset_id}}/prices"): (
        A.OWNED,
        lambda _: {"day": "2025-02-01", "close": "1.00"},
    ),
    ("DELETE", f"{V1}/assets/{{asset_id}}/prices/{{price_id}}"): (A.OWNED, None),
    ("GET", f"{V1}/transactions"): (A.SELF, None),
    ("POST", f"{V1}/transactions"): (A.OWNED, _tx),  # the owner's account and asset in the body
    ("POST", f"{V1}/transactions/preview"): (A.OWNED, lambda ids: {"transaction": _tx(ids)}),
    ("POST", f"{V1}/transactions/batch"): (A.OWNED, lambda ids: {"transactions": [_tx(ids)]}),
    ("POST", f"{V1}/transactions/batch/preview"): (
        A.OWNED,
        lambda ids: {"transactions": [_tx(ids)]},
    ),
    ("GET", f"{V1}/transactions/{{tx_id}}"): (A.OWNED, None),
    ("PUT", f"{V1}/transactions/{{tx_id}}"): (A.OWNED, _tx),
    ("DELETE", f"{V1}/transactions/{{tx_id}}"): (A.OWNED, None),
    ("POST", f"{V1}/transactions/bulk-edit"): (
        A.OWNED,
        lambda ids: {
            "edits": [
                {
                    "id": ids["tx_id"],
                    "quantity": "2",
                    "gross_amount": "20.00",
                    "total_amount": "20.00",
                }
            ]
        },
    ),
    ("POST", f"{V1}/transactions/bulk-delete"): (A.OWNED, lambda ids: {"ids": [ids["tx_id"]]}),
    ("DELETE", f"{V1}/accounts/{{account_id}}/assets/{{asset_id}}/transactions"): (A.OWNED, None),
    ("GET", f"{V1}/accounts/{{account_id}}/summary"): (A.OWNED, None),
    ("GET", f"{V1}/accounts/{{account_id}}/positions"): (A.OWNED, None),
    ("GET", f"{V1}/portfolio/summary"): (A.SELF, None),
    ("GET", f"{V1}/portfolio/positions"): (A.SELF, None),
    ("GET", f"{V1}/accounts/{{account_id}}/breakdown"): (A.OWNED, None),
    ("GET", f"{V1}/portfolio/breakdown"): (A.SELF, None),
    ("POST", f"{V1}/imports/preview"): (A.OWNED, _import),  # the owner's account in the body
    ("POST", f"{V1}/imports/confirm"): (A.OWNED, _confirm),
    ("GET", f"{V1}/imports"): (A.SELF, None),
    ("GET", f"{V1}/transactions/export.csv"): (A.SELF, None),
    ("GET", f"{V1}/imports/{{batch_id}}"): (A.OWNED, None),
    ("GET", f"{V1}/currencies"): (A.SELF, None),
}


def _cases(*kinds: Access) -> list:
    return [
        pytest.param(method, path, body, id=f"{method} {path}")
        for (method, path), (kind, body) in ROUTES.items()
        if kind in kinds
    ]


def test_every_route_is_in_the_matrix(app) -> None:
    """The OpenAPI document lists every route (FastAPI keeps included routers lazily, so the
    public way to enumerate them is the document; hidden routes are forbidden below)."""
    documented = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert documented - set(ROUTES) == set(), "add new routes to ROUTES with their access rule"
    assert set(ROUTES) - documented == set(), "ROUTES lists a route that no longer exists"


def test_no_route_is_hidden_from_the_openapi_document() -> None:
    source = Path(portfolio_tracker.__file__).parent
    hidden = [p for p in source.rglob("*.py") if "include_in_schema" in p.read_text()]
    assert hidden == [], "a hidden route would escape the matrix"


@pytest.fixture
def world(client: TestClient) -> dict:
    return _world(client)


def _world(client: TestClient) -> dict:
    """Alice owns an account, a private asset, a manual price, a transaction and an import
    batch. Bob is another investor; the Administrator owns nothing. Everyone uses bearer
    tokens."""
    create_admin(client)
    client.cookies.clear()
    admin = bearer(login_token(client, "admin"))
    alice, bob = investor(client, "alice"), investor(client, "bob")

    def ok(method: str, path: str, body: dict) -> dict:
        response = client.request(method, f"{V1}{path}", headers=alice, json=body)
        assert response.status_code in (200, 201), response.text
        return response.json()

    account = ok("POST", "/accounts", {"name": "Main", "currency": "EUR"})
    asset = ok("POST", "/assets", {"name": "Alice ETF", "asset_type": "ETF",
                                   "quote_currency": "EUR", "quote_unit": "CURRENCY",
                                   "isin": fake_isin(42)})  # fmt: skip
    price = ok("PUT", f"/assets/{asset['id']}/prices", {"day": "2025-01-31", "close": "11.00"})
    ids = {"account_id": account["id"], "asset_id": asset["id"], "price_id": price["id"]}
    ids["tx_id"] = ok("POST", "/transactions", _tx(ids))["transactions"][0]["id"]
    ids["user_id"] = client.get(f"{V1}/me", headers=alice).json()["id"]
    seen = ok("POST", "/imports/preview", _import(ids))
    done = ok("POST", "/imports/confirm", _import(ids) | {"fingerprint": seen["fingerprint"],
                                                          "selected": ["line:2"]})  # fmt: skip
    ids["batch_id"] = done["batches"][0]["id"]
    return {"ids": ids, "alice": alice, "bob": bob, "admin": admin}


def _call(client: TestClient, method: str, path: str, body: Body, ids: Ids, headers=None):
    return client.request(
        method, path.format(**ids), headers=headers, json=body(ids) if body else None
    )


def _snapshot(client: TestClient, alice_id: str) -> dict:
    """Alice's data and credentials, to prove a refused request changed nothing."""
    queries = {
        "accounts": "SELECT * FROM accounts ORDER BY id",
        "assets": "SELECT * FROM assets ORDER BY id",
        "transactions": "SELECT * FROM transactions ORDER BY id",
        "prices": "SELECT * FROM prices ORDER BY id",
        "import_batches": "SELECT * FROM import_batches ORDER BY id",
        "alice": "SELECT password_hash, must_change_password, disabled_at, theme FROM users "
        "WHERE id = :id",
        "alice_sessions": "SELECT token_hash FROM sessions WHERE user_id = :id ORDER BY 1",
    }
    with Session(client.app.state.engine) as db:
        return {k: db.execute(text(q), {"id": alice_id}).all() for k, q in queries.items()}


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.SELF, A.OWNED, A.ADMIN))
def test_without_a_session_every_private_route_is_401(
    client: TestClient, world, method, path, body
) -> None:
    response = _call(client, method, path, body, world["ids"])
    assert response.status_code == 401, response.text
    assert response.json()["code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.PUBLIC))
def test_public_routes_need_no_session(client: TestClient, world, method, path, body) -> None:
    response = _call(client, method, path, body, world["ids"])
    # A failed login, or a second step without the first, is a 401 of its own, not "no session".
    assert response.status_code != 401 or response.json()["code"] in (
        "INVALID_CREDENTIALS",
        "LOGIN_STEP_EXPIRED",
    )


@pytest.mark.parametrize("who", ["bob", "admin"])
@pytest.mark.parametrize(("method", "path", "body"), _cases(A.OWNED))
def test_others_get_404_on_owned_data_and_change_nothing(
    client: TestClient, world, who, method, path, body
) -> None:
    before = _snapshot(client, world["ids"]["user_id"])
    response = _call(client, method, path, body, world["ids"], world[who])
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert _snapshot(client, world["ids"]["user_id"]) == before


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_admin_routes_are_404_for_an_investor(
    client: TestClient, world, method, path, body
) -> None:
    before = _snapshot(client, world["ids"]["user_id"])
    response = _call(client, method, path, body, world["ids"], world["bob"])
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert _snapshot(client, world["ids"]["user_id"]) == before


def _temporary_password(client: TestClient, username: str) -> None:
    """As after a reset: the user must change the password before anything else."""
    with Session(client.app.state.engine) as db, db.begin():
        db.execute(text("UPDATE users SET must_change_password = 1 WHERE username = :u"),
                   {"u": username})  # fmt: skip


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_admin_routes_are_404_for_an_investor_with_a_temporary_password(
    client: TestClient, world, method, path, body
) -> None:
    """The role is checked first (D-93): a non-administrator never learns that an admin
    route exists, even while a password change is pending."""
    _temporary_password(client, "bob")
    before = _snapshot(client, world["ids"]["user_id"])
    response = _call(client, method, path, body, world["ids"], world["bob"])
    assert response.status_code == 404, response.text
    assert response.json()["code"] == "NOT_FOUND"
    assert _snapshot(client, world["ids"]["user_id"]) == before


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_admin_routes_ask_an_administrator_with_a_temporary_password_to_change_it(
    client: TestClient, world, method, path, body
) -> None:
    _temporary_password(client, "admin")
    before = _snapshot(client, world["ids"]["user_id"])
    response = _call(client, method, path, body, world["ids"], world["admin"])
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert _snapshot(client, world["ids"]["user_id"]) == before


@pytest.mark.parametrize("who", ["bob", "admin"])
@pytest.mark.parametrize(
    ("method", "path", "body"), [c for c in _cases(A.SELF, A.ADMIN) if c.values[0] == "GET"]
)
def test_reads_never_show_another_users_data(
    client: TestClient, world, who, method, path, body
) -> None:
    """Alice's account, asset, price and transaction ids never appear in what Bob or the
    Administrator can read (the admin user list may name Alice, never her data)."""
    response = _call(client, method, path, body, world["ids"], world[who])
    if response.status_code == 404:  # admin route for Bob
        return
    assert response.status_code == 200, response.text
    for name in ("account_id", "asset_id", "price_id", "tx_id", "batch_id"):
        assert world["ids"][name] not in response.text, name


# --- two-factor authentication is mandatory for administrators (D-174) --------------------

# What an administrator who has not enrolled yet may still call: who they are, the enrolment,
# the password change and logout.
OPEN_BEFORE_ENROLMENT = {
    ("GET", f"{V1}/me"),
    ("POST", f"{V1}/me/password"),
    ("POST", f"{V1}/me/two-factor/setup"),
    ("POST", f"{V1}/me/two-factor/confirm"),
    ("POST", f"{V1}/auth/logout"),
}


@pytest.fixture
def obliged(tmp_path: Path, fast_passwords, clock):
    """The same world on an app with the default `PT_REQUIRE_2FA=admins`: the Administrator
    has not enrolled."""
    settings = Settings(data_dir=tmp_path / "obliged", require_2fa="admins")
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, _world(client)


def test_the_open_routes_before_enrolment_exist() -> None:
    assert set(ROUTES) >= OPEN_BEFORE_ENROLMENT


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.SELF, A.OWNED, A.ADMIN))
def test_an_administrator_who_has_not_enrolled_can_only_enrol(obliged, method, path, body) -> None:
    """Checked on every route and every request, not at login: 403, and nothing changes."""
    client, world = obliged
    before = _snapshot(client, world["ids"]["user_id"])

    response = _call(client, method, path, body, world["ids"], world["admin"])

    if (method, path) in OPEN_BEFORE_ENROLMENT:
        assert response.status_code != 403, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"
        assert _snapshot(client, world["ids"]["user_id"]) == before


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.SELF, A.OWNED))
def test_the_obligation_does_not_touch_an_investor(obliged, method, path, body) -> None:
    client, world = obliged

    response = _call(client, method, path, body, world["ids"], world["alice"])

    assert response.status_code != 403, response.text
    assert response.status_code != 401, response.text


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_admin_routes_stay_404_for_an_investor_under_the_obligation(
    obliged, method, path, body
) -> None:
    """The role first (D-92): the obligation never tells an investor that a route exists."""
    client, world = obliged

    response = _call(client, method, path, body, world["ids"], world["bob"])

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_the_temporary_password_comes_before_the_enrolment(obliged, method, path, body) -> None:
    """Role, then temporary password, then enrolment (D-93, D-174)."""
    client, world = obliged
    _temporary_password(client, "admin")

    response = _call(client, method, path, body, world["ids"], world["admin"])

    assert (response.status_code, response.json()["code"]) == (403, "PASSWORD_CHANGE_REQUIRED")


@pytest.mark.parametrize(("method", "path", "body"), _cases(A.ADMIN))
def test_an_enrolled_administrator_is_let_through(obliged, method, path, body) -> None:
    client, world = obliged
    enable_two_factor(client, "admin")

    response = _call(client, method, path, body, world["ids"], world["admin"])

    assert response.status_code != 403, response.text


def test_with_the_obligation_off_an_administrator_without_two_factor_is_let_through(
    client: TestClient, world
) -> None:
    assert client.app.state.settings.require_2fa == "off"  # the tests' default (conftest)

    assert client.get(f"{V1}/admin/users", headers=world["admin"]).status_code == 200
