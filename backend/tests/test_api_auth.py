"""Setup wizard, login/logout, brute-force protection, profile and password endpoints."""

import logging

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, add_user, bearer, create_admin, investor, login_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.__main__ import main
from portfolio_tracker.api.deps import SESSION_COOKIE
from portfolio_tracker.cli import CliError, reset_password
from portfolio_tracker.db.models import AuditEntry, User, UserSession
from portfolio_tracker.main import create_app


def audit_actions(client: TestClient) -> list[str]:
    with Session(client.app.state.engine) as db:
        return list(db.scalars(select(AuditEntry.action).order_by(AuditEntry.at)))


def session_count(client: TestClient) -> int:
    with Session(client.app.state.engine) as db:
        return len(db.scalars(select(UserSession)).all())


# --- setup wizard -----------------------------------------------------------------------


def test_fresh_instance_requires_setup(client: TestClient) -> None:
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}


def test_setup_creates_the_administrator_and_logs_in(client: TestClient) -> None:
    body = create_admin(client, "Root", language="it")

    assert body["user"]["username"] == "Root"
    assert body["user"]["role"] == "ADMINISTRATOR"
    assert body["user"]["language"] == "it"
    assert body["user"]["theme"] == "moderno"
    assert body["user"]["reporting_currency"] == "EUR"
    assert body["token"] is None  # the web app gets the HttpOnly cookie only
    assert client.get("/api/v1/me").json()["username"] == "Root"
    assert client.get("/api/v1/setup/status").json() == {"setup_required": False}
    assert audit_actions(client) == ["setup.administrator_created"]


def test_session_cookie_flags(client: TestClient) -> None:
    response = client.post(
        "/api/v1/setup/admin",
        json={"username": "admin", "password": PASSWORD, "language": "en"},
    )
    cookie = response.headers["set-cookie"]

    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert flag in cookie


def test_setup_runs_only_once(client: TestClient) -> None:
    create_admin(client)

    again = client.post(
        "/api/v1/setup/admin",
        json={"username": "other", "password": PASSWORD, "language": "en"},
    )
    assert again.status_code == 409 and again.json()["code"] == "SETUP_ALREADY_DONE"
    restore = client.post("/api/v1/setup/restore")
    assert restore.status_code == 409


def test_restore_is_not_available_yet(client: TestClient) -> None:
    response = client.post("/api/v1/setup/restore")

    assert response.status_code == 501 and response.json()["code"] == "NOT_IMPLEMENTED"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"username": "admin", "password": "short", "language": "en"}, "PASSWORD_TOO_SHORT"),
        ({"username": "ad", "password": PASSWORD, "language": "en"}, "REQUEST_INVALID"),
        ({"username": "mario rossi", "password": PASSWORD, "language": "en"}, "REQUEST_INVALID"),
        ({"username": "màrio", "password": PASSWORD, "language": "en"}, "REQUEST_INVALID"),
        ({"username": "admin", "password": PASSWORD, "language": "de"}, "REQUEST_INVALID"),
        ({"username": "admin", "password": PASSWORD}, "REQUEST_INVALID"),
        (
            {"username": "admin", "password": PASSWORD, "language": "en", "role": "INVESTOR"},
            "REQUEST_INVALID",
        ),
    ],
)
def test_setup_validation(client: TestClient, body: dict, code: str) -> None:
    response = client.post("/api/v1/setup/admin", json=body)

    assert response.status_code == 422 and response.json()["code"] == code
    assert client.get("/api/v1/setup/status").json() == {"setup_required": True}


# --- login ------------------------------------------------------------------------------


def test_login_with_cookie_and_with_token(client: TestClient) -> None:
    create_admin(client)
    add_user(client, "alice")
    client.cookies.clear()

    response = client.post("/api/v1/auth/login", json={"username": "ALICE", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "alice"
    assert response.json()["token"] is None
    assert client.get("/api/v1/me").status_code == 200  # cookie

    client.cookies.clear()
    token = login_token(client, "alice")
    assert SESSION_COOKIE not in client.cookies  # token clients get no cookie
    assert client.get("/api/v1/me", headers=bearer(token)).json()["username"] == "alice"


def test_login_records_last_login(client: TestClient, clock) -> None:
    create_admin(client)
    add_user(client, "alice")
    token = login_token(client, "alice")

    me = client.get("/api/v1/me", headers=bearer(token)).json()
    assert me["last_login_at"] == clock.now.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong password!"), ("nobody", PASSWORD), ("disabled", PASSWORD), ("", PASSWORD)],
)
def test_failed_logins_look_the_same(client: TestClient, username: str, password: str) -> None:
    create_admin(client)
    add_user(client, "alice")
    add_user(client, "disabled", disabled=True)

    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json() == {
        "detail": "wrong username or password",
        "code": "INVALID_CREDENTIALS",
    }
    assert audit_actions(client)[-1] == "auth.login_failed"  # committed despite the 401


def test_unknown_user_still_costs_a_password_verification(client: TestClient) -> None:
    create_admin(client)
    calls = []
    passwords = client.app.state.passwords
    original = passwords.verify_dummy
    passwords.verify_dummy = lambda password: calls.append(password) or original(password)
    try:
        client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x" * 12})
    finally:
        passwords.verify_dummy = original

    assert calls == ["x" * 12]


def test_repeated_failures_are_throttled(client: TestClient, clock) -> None:
    create_admin(client)
    add_user(client, "alice")
    wrong = {"username": "alice", "password": "not the password"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=wrong).status_code == 401

    blocked = client.post("/api/v1/auth/login", json={"username": "alice", "password": PASSWORD})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert blocked.json()["retry_after"] == 60
    assert blocked.headers["Retry-After"] == "60"

    clock.advance(seconds=60)
    assert login_token(client, "alice")


def test_password_hash_is_upgraded_on_login(client: TestClient) -> None:
    create_admin(client)
    add_user(client, "alice")
    with Session(client.app.state.engine) as db, db.begin():
        user = db.scalars(select(User).where(User.username == "alice")).one()
        older = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, type=Type.I)
        user.password_hash = older.hash(PASSWORD)  # e.g. parameters of an earlier release
        weak_hash = user.password_hash

    login_token(client, "alice")

    with Session(client.app.state.engine) as db:
        new_hash = db.scalars(select(User.password_hash).where(User.username == "alice")).one()
    assert new_hash != weak_hash and new_hash.startswith("$argon2id$")
    assert login_token(client, "alice")


def cookie_warnings(app: FastAPI, base_url: str, caplog: pytest.LogCaptureFixture) -> int:
    with TestClient(app, base_url=base_url) as client:
        # The app's JSON log setup replaced the root handlers at startup: listen again.
        logging.getLogger().addHandler(caplog.handler)
        create_admin(client)  # the setup wizard logs in too
        for _ in range(2):
            client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    return len([r for r in caplog.records if "PT_COOKIE_SECURE" in r.getMessage()])


def test_cookie_warning_over_plain_http(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    assert cookie_warnings(app, "http://192.168.1.20:8000", caplog) == 1  # once per process


def test_no_cookie_warning_on_localhost(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    assert cookie_warnings(app, "http://localhost:8000", caplog) == 0


def test_no_cookie_warning_when_secure_is_off(
    settings, fast_passwords, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings.model_copy(update={"cookie_secure": False}), passwords=fast_passwords)

    assert cookie_warnings(app, "http://192.168.1.20:8000", caplog) == 0


# --- logout -----------------------------------------------------------------------------


def test_logout_ends_the_session_and_clears_the_cookie(client: TestClient) -> None:
    create_admin(client)

    without_csrf = client.post("/api/v1/auth/logout")
    assert without_csrf.status_code == 403
    response = client.post("/api/v1/auth/logout", headers=CSRF)
    assert response.status_code == 204
    assert SESSION_COOKIE not in client.cookies
    assert client.get("/api/v1/me").status_code == 401
    assert session_count(client) == 0


def test_logout_others_keeps_the_current_session(client: TestClient) -> None:
    create_admin(client)
    others = [login_token(client, "admin") for _ in range(2)]

    response = client.post("/api/v1/auth/logout-others", headers=CSRF)

    assert response.json() == {"revoked": 2}
    assert client.get("/api/v1/me").status_code == 200
    for token in others:
        assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


# --- profile, preferences, sessions -----------------------------------------------------


def test_update_preferences(client: TestClient) -> None:
    create_admin(client)
    headers = investor(client, "alice")

    response = client.patch(
        "/api/v1/me",
        headers=headers,
        json={"theme": "ambra", "language": "it", "reporting_currency": "USD",
              "display_name": "Alice"},
    )  # fmt: skip

    assert response.status_code == 200
    me = response.json()
    assert (me["theme"], me["language"], me["reporting_currency"], me["display_name"]) == (
        "ambra",
        "it",
        "USD",
        "Alice",
    )
    cleared = client.patch("/api/v1/me", headers=headers, json={"display_name": None})
    assert cleared.json()["display_name"] is None
    assert cleared.json()["theme"] == "ambra"  # untouched


@pytest.mark.parametrize(
    "body",
    [
        {"theme": "dark"},
        {"language": "fr"},
        {"reporting_currency": "eur"},
        {"reporting_currency": "XYZ"},
        {"theme": None},
        {"role": "ADMINISTRATOR"},
    ],
)
def test_invalid_preferences_are_rejected(client: TestClient, body: dict) -> None:
    create_admin(client)
    headers = investor(client, "alice")

    response = client.patch("/api/v1/me", headers=headers, json=body)

    assert response.status_code == 422
    assert client.get("/api/v1/me", headers=headers).json()["role"] == "INVESTOR"


def test_list_sessions_marks_the_current_one(client: TestClient) -> None:
    create_admin(client)
    login_token(client, "admin")

    sessions = client.get("/api/v1/me/sessions").json()

    assert len(sessions) == 2
    assert [s["current"] for s in sessions].count(True) == 1
    assert "token" not in sessions[0] and "token_hash" not in sessions[0]


# --- password change and temporary passwords ---------------------------------------------


def test_change_password_ends_other_sessions(client: TestClient) -> None:
    create_admin(client)
    other = login_token(client, "admin")
    new = "a brand new passphrase"

    response = client.post(
        "/api/v1/me/password",
        headers=CSRF,
        json={"current_password": PASSWORD, "new_password": new},
    )

    assert response.status_code == 200
    assert client.get("/api/v1/me").status_code == 200
    assert client.get("/api/v1/me", headers=bearer(other)).status_code == 401
    assert login_token(client, "admin", new)
    assert audit_actions(client)[-2] == "user.password_changed"


@pytest.mark.parametrize(
    ("current", "new", "code"),
    [
        ("wrong current pw", "a brand new passphrase", "CURRENT_PASSWORD_WRONG"),
        (PASSWORD, "too short", "PASSWORD_TOO_SHORT"),
        (PASSWORD, PASSWORD, "PASSWORD_UNCHANGED"),
    ],
)
def test_change_password_errors(client: TestClient, current: str, new: str, code: str) -> None:
    create_admin(client)

    response = client.post(
        "/api/v1/me/password",
        headers=CSRF,
        json={"current_password": current, "new_password": new},
    )

    assert response.status_code == 422 and response.json()["code"] == code


def test_temporary_password_must_be_changed_first(client: TestClient) -> None:
    create_admin(client)
    add_user(client, "newbie", must_change_password=True)
    headers = bearer(login_token(client, "newbie"))

    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200 and me.json()["must_change_password"] is True
    blocked = client.patch("/api/v1/me", headers=headers, json={"theme": "ambra"})
    assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"

    changed = client.post(
        "/api/v1/me/password",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "my own passphrase"},
    )
    assert changed.json()["must_change_password"] is False
    assert client.patch("/api/v1/me", headers=headers, json={"theme": "ambra"}).status_code == 200


# --- container command ------------------------------------------------------------------


def test_reset_password_command(client: TestClient, settings, fast_passwords) -> None:
    create_admin(client)

    password = reset_password(settings, "ADMIN", passwords=fast_passwords)

    assert client.get("/api/v1/me").status_code == 401  # every session ended
    token = login_token(client, "admin", password)
    assert client.get("/api/v1/me", headers=bearer(token)).json()["must_change_password"]
    assert audit_actions(client)[-2] == "user.password_reset"
    with pytest.raises(CliError):
        reset_password(settings, "nobody", passwords=fast_passwords)


def test_command_line_reset_password(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))

    assert main(["reset-password", "nobody"]) == 1
    assert "no user named 'nobody'" in capsys.readouterr().err
