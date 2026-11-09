"""Administration: user management (D-21, D-51, D-78). Invented users only."""

import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, bearer, create_admin, details, investor, login_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.db import BEGIN_OPTION
from portfolio_tracker.db.models import AuditEntry, User

TEMP = re.compile(r"([A-Za-z0-9]{4}-){3}[A-Za-z0-9]{4}")


def create_user(client: TestClient, username: str, **fields) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=CSRF,
        json={"username": username, **details(username), **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def ready_user(client: TestClient, username: str) -> tuple[dict, dict[str, str]]:
    """Created by the admin, temporary password already replaced; returns (user, headers)."""
    created = create_user(client, username)
    headers = bearer(login_token(client, username, created["temporary_password"]))
    changed = client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": created["temporary_password"], "new_password": PASSWORD},
    )
    assert changed.status_code == 200
    return created["user"], bearer(changed.json()["token"])  # the session's new token (D-164)


def audit_actions(client: TestClient) -> list[str]:
    with Session(client.app.state.engine) as db:
        return list(db.scalars(select(AuditEntry.action).order_by(AuditEntry.at)))


# --- listing and creating ---------------------------------------------------------------


def test_list_users_shows_account_data_only(client: TestClient) -> None:
    create_admin(client)
    create_user(client, "alice")

    users = client.get("/api/v1/admin/users").json()

    assert [u["username"] for u in users] == ["admin", "alice"]
    assert set(users[1]) == {
        "id", "username", "first_name", "last_name", "email", "role", "two_factor_enabled",
        "must_change_password", "disabled", "created_at", "last_login_at",
    }  # fmt: skip
    assert users[0]["role"] == "ADMINISTRATOR" and users[1]["role"] == "INVESTOR"


def test_create_user_returns_the_temporary_password_once(client: TestClient) -> None:
    create_admin(client, language="it")

    created = create_user(
        client,
        "alice",
        first_name="  Alice   Maria ",
        last_name="D'Àngelo",
        email="Alice@Example.ORG",
    )

    password = created["temporary_password"]
    assert TEMP.fullmatch(password)
    assert created["user"]["must_change_password"] is True
    with Session(client.app.state.engine) as db:
        stored = db.scalars(select(User).where(User.username == "alice")).one()
        # Normalised as D-173 says: names trimmed and collapsed, the e-mail lowercased.
        assert (stored.first_name, stored.last_name, stored.email) == (
            "Alice Maria",
            "D'Àngelo",
            "alice@example.org",
        )
        assert (
            stored.password_hash.startswith("$argon2id$") and password not in stored.password_hash
        )
        assert stored.language == "it"  # the administrator's language by default
    listed = client.get("/api/v1/admin/users").json()
    assert all(
        "password" not in key for user in listed for key in user if key != "must_change_password"
    )
    assert audit_actions(client)[-1] == "user.created"


def test_new_user_must_change_the_temporary_password(client: TestClient) -> None:
    create_admin(client)
    created = create_user(client, "alice", language="en")
    headers = bearer(login_token(client, "alice", created["temporary_password"]))

    blocked = client.get("/api/v1/me/sessions", headers=headers)
    assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    changed = client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": created["temporary_password"], "new_password": PASSWORD},
    )
    assert client.get("/api/v1/me/sessions", headers=headers).status_code == 401  # old token
    headers = bearer(changed.json()["token"])
    assert client.get("/api/v1/me/sessions", headers=headers).status_code == 200


def test_create_another_administrator(client: TestClient) -> None:
    create_admin(client)
    _, headers = ready_user(client, "bob")  # investor first
    create_user(client, "carol", role="ADMINISTRATOR")

    roles = {u["username"]: u["role"] for u in client.get("/api/v1/admin/users").json()}
    assert roles == {"admin": "ADMINISTRATOR", "bob": "INVESTOR", "carol": "ADMINISTRATOR"}
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 404


def test_usernames_are_unique_ignoring_case(client: TestClient) -> None:
    """Through the case-folded key (D-173): also beyond ASCII."""
    create_admin(client)
    create_user(client, "Àlex")

    body = {"username": "àLEX", **details("someone-else")}
    response = client.post("/api/v1/admin/users", headers=CSRF, json=body)

    assert response.status_code == 409 and response.json()["code"] == "USERNAME_TAKEN"
    assert [u["username"] for u in client.get("/api/v1/admin/users").json()] == ["admin", "Àlex"]


def test_e_mails_are_unique_ignoring_case(client: TestClient) -> None:
    create_admin(client)
    create_user(client, "alice", email="alice@example.org")

    body = {"username": "alicia", **details("alicia"), "email": "ALICE@example.org"}
    response = client.post("/api/v1/admin/users", headers=CSRF, json=body)

    assert response.status_code == 409 and response.json()["code"] == "EMAIL_TAKEN"


def test_an_e_mail_can_be_a_username(client: TestClient) -> None:
    create_admin(client)
    created = create_user(client, "Mario.Rossi+casa@Example.org")

    password = created["temporary_password"]  # found ignoring case, stored as typed
    assert login_token(client, "mario.rossi+casa@example.org", password)
    assert created["user"]["username"] == "Mario.Rossi+casa@Example.org"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a b"},
        {"role": "ROOT"},
        {"language": "de"},
        {"password": "chosen by admin"},  # passwords are generated
        {"first_name": " "},
        {"last_name": "Rossi3"},
        {"email": "alice"},
        {"display_name": "Alice"},  # gone (D-173)
    ],
)
def test_create_user_validation(client: TestClient, body: dict) -> None:
    create_admin(client)

    full = {"username": "alice", **details("alice"), **body}
    response = client.post("/api/v1/admin/users", headers=CSRF, json=full)

    assert response.status_code == 422
    assert len(client.get("/api/v1/admin/users").json()) == 1


def test_create_user_hashes_outside_the_write_lock(client: TestClient) -> None:
    create_admin(client)
    passwords = client.app.state.passwords
    original = passwords.hash
    writer_ok = []

    def hash_while_writing(password: str) -> str:
        writer = client.app.state.engine.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})
        with writer.connect() as connection:
            connection.connection.driver_connection.execute("PRAGMA busy_timeout=0")
            with connection.begin():
                connection.exec_driver_sql("SELECT 1")
            connection.connection.driver_connection.execute("PRAGMA busy_timeout=5000")
        writer_ok.append(True)
        return original(password)

    passwords.hash = hash_while_writing
    try:
        create_user(client, "alice")
    finally:
        passwords.hash = original
    assert writer_ok == [True]


# --- reset, disable, enable -------------------------------------------------------------


def test_reset_password(client: TestClient) -> None:
    create_admin(client)
    alice, headers = ready_user(client, "alice")

    response = client.post(f"/api/v1/admin/users/{alice['id']}/reset-password", headers=CSRF)

    assert response.status_code == 200
    temporary = response.json()["temporary_password"]
    assert TEMP.fullmatch(temporary)
    assert response.json()["user"]["must_change_password"] is True
    assert client.get("/api/v1/me", headers=headers).status_code == 401  # sessions ended
    old = client.post(
        "/api/v1/auth/login", headers=CSRF, json={"username": "alice", "password": PASSWORD}
    )
    assert old.status_code == 401
    assert login_token(client, "alice", temporary)
    assert audit_actions(client).count("user.password_reset") == 1


def test_disable_and_enable(client: TestClient) -> None:
    create_admin(client)
    alice, headers = ready_user(client, "alice")

    disabled = client.post(f"/api/v1/admin/users/{alice['id']}/disable", headers=CSRF)
    assert disabled.status_code == 200 and disabled.json()["disabled"] is True
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    refused = client.post(
        "/api/v1/auth/login", headers=CSRF, json={"username": "alice", "password": PASSWORD}
    )
    assert refused.json()["code"] == "INVALID_CREDENTIALS"  # same answer as a wrong password
    again = client.post(f"/api/v1/admin/users/{alice['id']}/disable", headers=CSRF)
    assert again.status_code == 200  # idempotent

    enabled = client.post(f"/api/v1/admin/users/{alice['id']}/enable", headers=CSRF)
    assert enabled.json()["disabled"] is False
    assert login_token(client, "alice")
    actions = audit_actions(client)
    assert actions.count("user.disabled") == 1 and actions.count("user.enabled") == 1


def test_administrator_cannot_disable_themself(client: TestClient) -> None:
    admin = create_admin(client)["user"]

    response = client.post(f"/api/v1/admin/users/{admin['id']}/disable", headers=CSRF)

    assert response.status_code == 409 and response.json()["code"] == "CANNOT_DISABLE_SELF"
    assert client.get("/api/v1/me").status_code == 200


def test_another_administrator_can_be_disabled(client: TestClient) -> None:
    create_admin(client)
    carol = create_user(client, "carol", role="ADMINISTRATOR")["user"]

    response = client.post(f"/api/v1/admin/users/{carol['id']}/disable", headers=CSRF)

    assert response.status_code == 200


@pytest.mark.parametrize("action", ["reset-password", "disable", "enable"])
def test_unknown_user_is_404(client: TestClient, action: str) -> None:
    create_admin(client)

    response = client.post(f"/api/v1/admin/users/{uuid4()}/{action}", headers=CSRF)

    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"


# --- access control ---------------------------------------------------------------------

ADMIN_CALLS = [
    ("GET", "/api/v1/admin/users", None),
    ("POST", "/api/v1/admin/users", {"username": "mallory", **details("mallory")}),
    ("POST", "/api/v1/admin/users/{id}/reset-password", None),
    ("POST", "/api/v1/admin/users/{id}/disable", None),
    ("POST", "/api/v1/admin/users/{id}/enable", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_CALLS)
def test_investors_get_404_on_every_admin_endpoint(client, method, path, body) -> None:
    admin = create_admin(client)["user"]
    headers = investor(client, "alice")

    response = client.request(method, path.format(id=admin["id"]), headers=headers, json=body)

    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"
    assert [u["username"] for u in client.get("/api/v1/admin/users").json()] == ["admin", "alice"]


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_CALLS)
def test_admin_endpoints_need_a_session(client, method, path, body) -> None:
    admin = create_admin(client)["user"]
    client.cookies.clear()

    response = client.request(method, path.format(id=admin["id"]), json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_CALLS)
def test_admin_with_temporary_password_must_change_it_first(client, method, path, body) -> None:
    create_admin(client)
    carol = create_user(client, "carol", role="ADMINISTRATOR")
    headers = bearer(login_token(client, "carol", carol["temporary_password"]))

    response = client.request(
        method, path.format(id=carol["user"]["id"]), headers=headers, json=body
    )

    assert response.status_code == 403 and response.json()["code"] == "PASSWORD_CHANGE_REQUIRED"


def test_cookie_admin_writes_need_the_csrf_header(client: TestClient) -> None:
    create_admin(client)

    response = client.post("/api/v1/admin/users", json={"username": "alice", **details("alice")})

    assert response.status_code == 403 and response.json()["code"] == "CSRF_CHECK_FAILED"


# --- the role is read from the database on every request (D-173) ------------------------------


def set_role(client: TestClient, username: str, role: str) -> None:
    """What a later role change (not in rc.2) would do: only the users row changes."""
    with Session(client.app.state.engine) as db, db.begin():
        db.scalars(select(User).where(User.username == username)).one().role = role


def test_a_role_changed_in_the_database_applies_to_the_next_request(client: TestClient) -> None:
    """The session holds no role: the same token gains and loses the administrator's routes
    at once, and `/me` says so, so a role change needs no new login (D-173 (1))."""
    create_admin(client)
    headers = investor(client, "alice")
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 404

    set_role(client, "alice", "ADMINISTRATOR")
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 200
    assert client.get("/api/v1/me", headers=headers).json()["role"] == "ADMINISTRATOR"

    set_role(client, "alice", "INVESTOR")
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 404
    assert client.get("/api/v1/me", headers=headers).json()["role"] == "INVESTOR"
