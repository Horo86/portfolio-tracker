"""Administration: user management (D-21, D-51, D-78). Invented users only."""

import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, bearer, create_admin, investor, login_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.db import BEGIN_OPTION
from portfolio_tracker.db.models import AuditEntry, User

TEMP = re.compile(r"([A-Za-z0-9]{4}-){3}[A-Za-z0-9]{4}")


def create_user(client: TestClient, username: str, **fields) -> dict:
    response = client.post(
        "/api/v1/admin/users", headers=CSRF, json={"username": username, **fields}
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
    return created["user"], headers


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
        "id", "username", "display_name", "role", "two_factor_enabled",
        "must_change_password", "disabled", "created_at", "last_login_at",
    }  # fmt: skip
    assert users[0]["role"] == "ADMINISTRATOR" and users[1]["role"] == "INVESTOR"


def test_create_user_returns_the_temporary_password_once(client: TestClient) -> None:
    create_admin(client, language="it")

    created = create_user(client, "alice", display_name="Alice")

    password = created["temporary_password"]
    assert TEMP.fullmatch(password)
    assert created["user"]["must_change_password"] is True
    with Session(client.app.state.engine) as db:
        stored = db.scalars(select(User).where(User.username == "alice")).one()
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
    client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": created["temporary_password"], "new_password": PASSWORD},
    )
    assert client.get("/api/v1/me/sessions", headers=headers).status_code == 200


def test_create_another_administrator(client: TestClient) -> None:
    create_admin(client)
    _, headers = ready_user(client, "bob")  # investor first
    create_user(client, "carol", role="ADMINISTRATOR")

    roles = {u["username"]: u["role"] for u in client.get("/api/v1/admin/users").json()}
    assert roles == {"admin": "ADMINISTRATOR", "bob": "INVESTOR", "carol": "ADMINISTRATOR"}
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 404


def test_usernames_are_unique_ignoring_case(client: TestClient) -> None:
    create_admin(client)
    create_user(client, "alice")

    response = client.post("/api/v1/admin/users", headers=CSRF, json={"username": "ALICE"})

    assert response.status_code == 409 and response.json()["code"] == "USERNAME_TAKEN"


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a b"},
        {"username": "alice", "role": "ROOT"},
        {"username": "alice", "language": "de"},
        {"username": "alice", "password": "chosen by admin"},  # passwords are generated
    ],
)
def test_create_user_validation(client: TestClient, body: dict) -> None:
    create_admin(client)

    response = client.post("/api/v1/admin/users", headers=CSRF, json=body)

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
    old = client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert old.status_code == 401
    assert login_token(client, "alice", temporary)
    assert audit_actions(client).count("user.password_reset") == 1


def test_disable_and_enable(client: TestClient) -> None:
    create_admin(client)
    alice, headers = ready_user(client, "alice")

    disabled = client.post(f"/api/v1/admin/users/{alice['id']}/disable", headers=CSRF)
    assert disabled.status_code == 200 and disabled.json()["disabled"] is True
    assert client.get("/api/v1/me", headers=headers).status_code == 401
    refused = client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
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
    ("POST", "/api/v1/admin/users", {"username": "mallory"}),
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

    response = client.post("/api/v1/admin/users", json={"username": "alice"})

    assert response.status_code == 403 and response.json()["code"] == "CSRF_CHECK_FAILED"
