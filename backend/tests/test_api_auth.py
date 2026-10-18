"""Setup wizard, login/logout, brute-force protection, profile and password endpoints."""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, add_user, bearer, create_admin, investor, login_token
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from portfolio_tracker.__main__ import main, serve
from portfolio_tracker.api.deps import SESSION_COOKIE
from portfolio_tracker.auth.sessions import delete_expired
from portfolio_tracker.cli import CliError, reset_password
from portfolio_tracker.config import Settings
from portfolio_tracker.db import BEGIN_OPTION, db_transaction
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


# --- review fixes (checkpoint B) --------------------------------------------------------


@contextmanager
def failing_commits():
    """Every ORM commit fails, as when SQLite stays locked past the busy timeout."""

    def boom(_session) -> None:
        raise OperationalError("COMMIT", {}, Exception("database is locked"))

    event.listen(Session, "before_commit", boom)
    try:
        yield
    finally:
        event.remove(Session, "before_commit", boom)


def test_a_failed_commit_is_never_reported_as_success(app: FastAPI) -> None:
    """The commit runs before the response is sent (fix 1): a failure is a 500."""
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        create_admin(c)
        with failing_commits():
            patched = c.patch("/api/v1/me", headers=CSRF, json={"theme": "ambra"})
            logout = c.post("/api/v1/auth/logout", headers=CSRF)

        assert patched.status_code == 500 and logout.status_code == 500
        assert c.get("/api/v1/me").json()["theme"] == "moderno"  # nothing was saved
        assert session_count(c) == 1  # the session survived the failed logout


def test_a_failed_commit_during_setup_creates_no_session(app: FastAPI) -> None:
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as c:
        with failing_commits():
            response = c.post(
                "/api/v1/setup/admin",
                json={"username": "admin", "password": PASSWORD, "language": "en"},
            )

        assert response.status_code == 500
        assert "set-cookie" not in response.headers
        assert c.get("/api/v1/setup/status").json() == {"setup_required": True}


def test_login_does_not_hold_the_write_lock_while_hashing(client: TestClient) -> None:
    """Another writer can commit while a login is verifying the password (fix 2)."""
    create_admin(client)
    passwords = client.app.state.passwords
    original = passwords.verify
    other_writer_ok = []

    def verify_while_writing(password_hash: str, password: str) -> bool:
        writer = client.app.state.engine.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})
        with writer.connect() as connection:
            connection.connection.driver_connection.execute("PRAGMA busy_timeout=0")
            with connection.begin():
                connection.exec_driver_sql("SELECT 1")
            connection.connection.driver_connection.execute("PRAGMA busy_timeout=5000")
        other_writer_ok.append(True)
        return original(password_hash, password)

    passwords.verify = verify_while_writing
    try:
        assert login_token(client, "admin")
    finally:
        passwords.verify = original
    assert other_writer_ok == [True]


def test_concurrent_wrong_logins_admit_only_the_free_attempts(client: TestClient) -> None:
    """A burst of parallel guesses on one username: at most 5 reach the password check."""
    create_admin(client)
    passwords = client.app.state.passwords
    original = passwords.verify
    checks, running, overlap = [], [0], [0]
    lock = threading.Lock()

    def slow_verify(password_hash: str, password: str) -> bool:
        with lock:
            checks.append(password)
            running[0] += 1
            overlap[0] = max(overlap[0], running[0])
        time.sleep(0.2)  # every guess is still being checked when the others arrive
        with lock:
            running[0] -= 1
        return original(password_hash, password)

    passwords.verify = slow_verify
    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            statuses = list(
                pool.map(
                    lambda i: (
                        client.post(
                            "/api/v1/auth/login",
                            json={"username": "admin", "password": f"wrong guess {i:04d}"},
                        ).status_code
                    ),
                    range(20),
                )
            )
    finally:
        passwords.verify = original

    assert overlap[0] > 1  # the requests really ran in parallel
    assert len(checks) <= 5
    assert statuses.count(401) == len(checks)
    assert statuses.count(429) == 20 - len(checks)


def test_password_change_is_throttled_and_audited(client: TestClient) -> None:
    """A stolen session cannot guess the current password without limit (fix 3)."""
    create_admin(client)
    wrong = {"current_password": "a wrong guess!", "new_password": "a brand new passphrase"}
    for _ in range(5):
        response = client.post("/api/v1/me/password", headers=CSRF, json=wrong)
        assert response.json()["code"] == "CURRENT_PASSWORD_WRONG"

    right = {"current_password": PASSWORD, "new_password": "a brand new passphrase"}
    blocked = client.post("/api/v1/me/password", headers=CSRF, json=right)
    assert blocked.status_code == 429 and blocked.json()["code"] == "TOO_MANY_ATTEMPTS"
    assert blocked.headers["Retry-After"] == "60"
    assert audit_actions(client).count("user.password_change_failed") == 5
    assert login_token(client, "admin")  # the login throttle is a separate key


def test_password_change_succeeds_after_the_block(client: TestClient, clock) -> None:
    create_admin(client)
    wrong = {"current_password": "a wrong guess!", "new_password": "a brand new passphrase"}
    for _ in range(5):
        client.post("/api/v1/me/password", headers=CSRF, json=wrong)
    clock.advance(seconds=60)

    right = {"current_password": PASSWORD, "new_password": "a brand new passphrase"}
    assert client.post("/api/v1/me/password", headers=CSRF, json=right).status_code == 200


def test_reads_survive_a_busy_database_when_refreshing_last_use(client: TestClient, clock) -> None:
    """A GET whose session is due for a refresh while another writer holds the lock (fix 4)."""
    create_admin(client)
    clock.advance(minutes=6)
    writer = client.app.state.engine.execution_options(**{BEGIN_OPTION: "IMMEDIATE"})

    with writer.begin():  # another request is writing
        response = client.get("/api/v1/me")

    assert response.status_code == 200


def test_bearer_wins_over_cookie(client: TestClient) -> None:
    create_admin(client)  # valid cookie

    response = client.get("/api/v1/me", headers=bearer("not-a-valid-token"))

    assert response.status_code == 401


@pytest.mark.parametrize("typed", ["my secret passphrase", "x" * 64])
def test_invalid_usernames_are_not_logged_verbatim(
    client: TestClient, typed: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A password typed in the username field must not reach the logs (fix 8)."""
    create_admin(client)
    logging.getLogger().addHandler(caplog.handler)
    with caplog.at_level(logging.WARNING):
        client.post("/api/v1/auth/login", json={"username": typed, "password": PASSWORD})

    with Session(client.app.state.engine) as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.action == "auth.login_failed")).one()
    assert entry.after == {"username": "(invalid)"}
    assert typed not in caplog.text


def test_server_ignores_forwarded_headers(monkeypatch) -> None:
    """Until trusted proxies exist (M6) the client IP is the direct peer (fix 5)."""
    captured = {}
    monkeypatch.setattr(
        "portfolio_tracker.__main__.uvicorn.run", lambda *a, **kw: captured.update(kw)
    )

    serve(Settings())

    assert captured["proxy_headers"] is False


def test_expired_sessions_are_deleted_by_the_cleanup(client: TestClient, clock) -> None:
    """The background loop's daily cleanup removes exactly the sessions `resolve` rejects."""
    create_admin(client)
    engine = client.app.state.engine
    policy = client.app.state.session_policy
    with db_transaction(engine, write=False) as session:
        assert len(session.scalars(select(UserSession)).all()) == 1
    with db_transaction(engine, write=True) as session:
        assert delete_expired(session, clock.now + policy.idle - timedelta(seconds=1), policy) == 0
        assert delete_expired(session, clock.now + policy.idle, policy) == 1
