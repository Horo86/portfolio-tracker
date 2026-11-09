"""D-165: after an administrator reset the user is told at the next login, with the date,
until the temporary password is replaced; and every admin action is audited with the acting
administrator and the target user, never a password. Invented users only."""

import json

from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, create_admin, details
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.cli import reset_password
from portfolio_tracker.db.models import AuditEntry, User

NEW = "my own new passphrase"


def create_user(client: TestClient, username: str, **fields) -> dict:
    response = client.post(
        "/api/v1/admin/users",
        headers=CSRF,
        json={"username": username, **details(username), **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def admin_reset(client: TestClient, user_id: str) -> str:
    response = client.post(f"/api/v1/admin/users/{user_id}/reset-password", headers=CSRF)
    assert response.status_code == 200, response.text
    return response.json()["temporary_password"]


def fresh_login(app, username: str, password: str) -> tuple[TestClient, dict]:
    """The user's own browser: a client of its own, with its own cookie."""
    browser = TestClient(app, base_url="https://testserver")
    response = browser.post(
        "/api/v1/auth/login", headers=CSRF, json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return browser, response.json()["user"]


def change(browser: TestClient, current: str, new: str = NEW) -> dict:
    response = browser.post(
        "/api/v1/me/password",
        headers=CSRF,
        json={"current_password": current, "new_password": new},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_user_is_told_of_an_administrator_reset_until_the_password_is_replaced(
    client: TestClient, clock
) -> None:
    create_admin(client)
    alice = create_user(client, "alice")
    browser, me = fresh_login(client.app, "alice", alice["temporary_password"])
    assert me["password_reset_at"] is None  # a new user: no notice
    change(browser, alice["temporary_password"])

    clock.advance(days=3)
    reset_at = clock.now
    temporary = admin_reset(client, alice["user"]["id"])
    clock.advance(hours=2)

    browser, me = fresh_login(client.app, "alice", temporary)
    assert me["must_change_password"] is True
    assert me["password_reset_at"] == reset_at.isoformat().replace("+00:00", "Z")
    assert browser.get("/api/v1/me").json()["password_reset_at"] == me["password_reset_at"]
    # Logging in again before the change: still told.
    browser, again = fresh_login(client.app, "alice", temporary)
    assert again["password_reset_at"] == me["password_reset_at"]

    changed = change(browser, temporary)

    assert changed["password_reset_at"] is None
    assert browser.get("/api/v1/me").json()["password_reset_at"] is None
    _, later = fresh_login(client.app, "alice", NEW)
    assert later["password_reset_at"] is None  # gone for good


def test_the_notice_names_no_administrator(client: TestClient) -> None:
    create_admin(client)
    alice = create_user(client, "alice")
    temporary = admin_reset(client, alice["user"]["id"])

    _, me = fresh_login(client.app, "alice", temporary)

    assert "admin" not in json.dumps(me).replace('"role": "INVESTOR"', "")


def test_the_container_command_sets_the_same_notice(
    client: TestClient, settings, fast_passwords
) -> None:
    create_admin(client)
    create_user(client, "alice")

    temporary = reset_password(settings, "alice", passwords=fast_passwords)

    _, me = fresh_login(client.app, "alice", temporary)
    assert me["password_reset_at"] is not None


def audit_entries(client: TestClient) -> list[AuditEntry]:
    with Session(client.app.state.engine) as db:
        return list(db.scalars(select(AuditEntry)))


def test_every_admin_action_is_audited_with_actor_and_target_and_no_password(
    client: TestClient,
) -> None:
    admin = create_admin(client)["user"]
    alice = create_user(client, "alice")
    target = alice["user"]["id"]
    temporary = admin_reset(client, target)
    client.post(f"/api/v1/admin/users/{target}/disable", headers=CSRF)
    client.post(f"/api/v1/admin/users/{target}/enable", headers=CSRF)

    entries = {e.action: e for e in audit_entries(client) if e.entity_type == "user"}
    for action in ("user.created", "user.password_reset", "user.disabled", "user.enabled"):
        entry = entries[action]
        assert str(entry.actor_user_id) == admin["id"], action
        assert str(entry.entity_id) == target, action
        assert entry.after["username"] == "alice", action
    with Session(client.app.state.engine) as db:
        hashes = list(db.scalars(select(User.password_hash)))
    everything = json.dumps(
        [[e.action, e.before, e.after] for e in audit_entries(client)], default=str
    )
    for secret in (alice["temporary_password"], temporary, PASSWORD, *hashes):
        assert secret not in everything
