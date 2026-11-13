"""The Administrator and other users' two-factor authentication (D-174): the column of the
Users screen, disabling it for someone else, never for oneself, and the password reset that
leaves it alone. Also the container command's `--disable-2fa`. Invented users only."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import (
    CSRF,
    PASSWORD,
    add_user,
    bearer,
    code_now,
    create_admin,
    enable_two_factor,
    login_token,
    wrong_code,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.__main__ import main
from portfolio_tracker.cli import reset_password
from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import AuditEntry, RecoveryCode, TwoFactor, UserSession
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import two_factor, users

USERS = "/api/v1/admin/users"
LOGIN = "/api/v1/auth/login"


def user_id(client: TestClient, username: str) -> str:
    with Session(client.app.state.engine) as db:
        return str(users.get_by_username(db, username).id)


def disable_2fa(client: TestClient, username: str, **body):
    """As the administrator without two-factor authentication: the password alone."""
    return client.post(
        f"{USERS}/{user_id(client, username)}/disable-2fa",
        headers=CSRF,
        json={"password": PASSWORD, **body},
    )


def reset(client: TestClient, username: str, **body):
    return client.post(
        f"{USERS}/{user_id(client, username)}/reset-password",
        headers=CSRF,
        json={"password": PASSWORD, **body},
    )


def listed(client: TestClient) -> dict[str, bool]:
    return {u["username"]: u["two_factor_enabled"] for u in client.get(USERS).json()}


def audit(client: TestClient) -> list[tuple[str, str | None, dict | None]]:
    with Session(client.app.state.engine) as db:
        rows = db.scalars(select(AuditEntry).order_by(AuditEntry.at, AuditEntry.id))
        return [
            (r.action, str(r.actor_user_id) if r.actor_user_id else None, r.after) for r in rows
        ]


def rows(client: TestClient, model) -> int:
    with Session(client.app.state.engine) as db:
        return len(db.scalars(select(model)).all())


def two_step_login(client: TestClient, username: str, secret: str, password: str = PASSWORD):
    saved, client.cookies = client.cookies, type(client.cookies)()
    try:
        step = client.post(
            LOGIN, headers=CSRF,
            json={"username": username, "password": password, "issue_token": True},
        )  # fmt: skip
        assert step.json().get("two_factor_required") is True, step.text
        return client.post(
            f"{LOGIN}/two-factor", headers=CSRF,
            json={"code": code_now(client, secret), "challenge": step.json()["challenge"]},
        )  # fmt: skip
    finally:
        client.cookies = saved


@pytest.fixture
def alice(client: TestClient) -> tuple[str, list[str]]:
    """The administrator is logged in (cookie); alice has two-factor authentication."""
    create_admin(client)
    add_user(client, "alice")
    return enable_two_factor(client, "alice")


# --- the Users screen's column --------------------------------------------------------------


def test_the_list_says_who_has_two_factor_and_nothing_more(client: TestClient, alice) -> None:
    secret, codes = alice
    add_user(client, "bob")

    response = client.get(USERS)

    assert listed(client) == {"admin": False, "alice": True, "bob": False}
    assert secret not in response.text and not any(code in response.text for code in codes)


def test_a_pending_enrolment_is_listed_as_off(client: TestClient, alice) -> None:
    add_user(client, "bob")
    with Session(client.app.state.engine) as db, db.begin():
        bob = users.get_by_username(db, "bob")
        two_factor.begin(db, bob.id, "A" * 32, client.app.state.clock())

    assert listed(client)["bob"] is False


# --- disabling another user's two-factor authentication --------------------------------------


def test_the_administrator_disables_another_user_s_two_factor(
    client: TestClient, alice, clock
) -> None:
    secret, _ = alice
    token = two_step_login(client, "alice", secret).json()["token"]
    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 200

    response = disable_2fa(client, "alice")

    assert response.status_code == 200 and response.json()["two_factor_enabled"] is False
    assert listed(client)["alice"] is False
    assert rows(client, TwoFactor) == 0 and rows(client, RecoveryCode) == 0
    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401  # sessions ended
    admin_id = user_id(client, "admin")
    assert (
        "user.two_factor_disabled", admin_id,
        {"username": "alice", "by": "administrator", "was_enabled": True,
         "pending_secret_removed": False, "sessions_revoked": 1},
    ) in audit(client)  # fmt: skip

    # Alice logs in with the password alone, and is told the date, never by whom.
    me = client.get("/api/v1/me", headers=bearer(login_token(client, "alice"))).json()
    assert me["two_factor_enabled"] is False
    assert me["two_factor_disabled_at"].startswith(clock.now.strftime("%Y-%m-%dT%H:%M"))
    assert "admin" not in str(me["two_factor_disabled_at"])


def test_disabling_drops_a_login_waiting_for_its_code(client: TestClient, alice) -> None:
    secret, _ = alice
    saved, client.cookies = client.cookies, type(client.cookies)()
    step = client.post(
        LOGIN, headers=CSRF, json={"username": "alice", "password": PASSWORD, "issue_token": True}
    )
    client.cookies = saved

    disable_2fa(client, "alice")

    late = client.post(
        f"{LOGIN}/two-factor", headers=CSRF,
        json={"code": code_now(client, secret), "challenge": step.json()["challenge"]},
    )  # fmt: skip
    assert (late.status_code, late.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")


def test_never_on_one_s_own_row(client: TestClient, alice) -> None:
    admin_secret, _ = enable_two_factor(client, "admin")

    response = disable_2fa(client, "admin", code=code_now(client, admin_secret))

    assert (response.status_code, response.json()["code"]) == (409, "CANNOT_DISABLE_OWN_2FA")
    assert listed(client)["admin"] is True and rows(client, TwoFactor) == 2
    assert client.get("/api/v1/me").json()["two_factor_disabled_at"] is None


def test_an_administrator_can_disable_another_administrator_s(client: TestClient, alice) -> None:
    add_user(client, "root", role="ADMINISTRATOR")
    enable_two_factor(client, "root")

    assert disable_2fa(client, "root").status_code == 200
    assert listed(client)["root"] is False


def test_disabling_what_is_off_changes_nothing_and_leaves_no_notice(
    client: TestClient, alice
) -> None:
    add_user(client, "bob")
    bob = bearer(login_token(client, "bob"))
    before = len(audit(client))

    response = disable_2fa(client, "bob")

    assert response.status_code == 200 and response.json()["two_factor_enabled"] is False
    assert len(audit(client)) == before
    me = client.get("/api/v1/me", headers=bob)  # the session is still there
    assert me.status_code == 200 and me.json()["two_factor_disabled_at"] is None


def test_an_unknown_user_is_404(client: TestClient, alice) -> None:
    response = client.post(
        f"{USERS}/00000000-0000-4000-8000-000000000000/disable-2fa",
        headers=CSRF,
        json={"password": PASSWORD},
    )

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


def test_an_investor_gets_404_and_nothing_changes(client: TestClient, alice) -> None:
    add_user(client, "bob")
    bob = bearer(login_token(client, "bob"))

    response = client.post(
        f"{USERS}/{user_id(client, 'alice')}/disable-2fa", headers=bob, json={"password": PASSWORD}
    )

    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")
    assert listed(client)["alice"] is True and rows(client, RecoveryCode) == 10


def test_the_cookie_call_needs_the_csrf_header(client: TestClient, alice) -> None:
    response = client.post(
        f"{USERS}/{user_id(client, 'alice')}/disable-2fa", json={"password": PASSWORD}
    )

    assert (response.status_code, response.json()["code"]) == (403, "CSRF_CHECK_FAILED")
    assert listed(client)["alice"] is True


# --- a password reset leaves two-factor authentication alone ---------------------------------


def test_an_administrator_reset_does_not_clear_two_factor(client: TestClient, alice) -> None:
    secret, _ = alice

    done = reset(client, "alice")

    assert done.status_code == 200 and done.json()["user"]["two_factor_enabled"] is True
    temporary = done.json()["temporary_password"]
    assert rows(client, TwoFactor) == 1 and rows(client, RecoveryCode) == 10
    logged = two_step_login(client, "alice", secret, temporary)  # the code is still asked
    assert logged.status_code == 200 and logged.json()["user"]["must_change_password"] is True
    assert logged.json()["user"]["two_factor_disabled_at"] is None


def test_the_command_without_the_flag_does_not_clear_two_factor(
    client: TestClient, alice, settings: Settings, fast_passwords
) -> None:
    secret, _ = alice

    temporary = reset_password(settings, "alice", passwords=fast_passwords)

    assert rows(client, TwoFactor) == 1 and rows(client, RecoveryCode) == 10
    assert two_step_login(client, "alice", secret, temporary).status_code == 200


# --- the container command's --disable-2fa ----------------------------------------------------


def test_the_command_with_the_flag_removes_two_factor_and_leaves_a_notice(
    client: TestClient, alice, settings: Settings, fast_passwords
) -> None:
    secret, _ = alice
    old = two_step_login(client, "alice", secret).json()["token"]

    temporary = reset_password(settings, "alice", disable_2fa=True, passwords=fast_passwords)

    assert rows(client, TwoFactor) == 0 and rows(client, RecoveryCode) == 0
    assert client.get("/api/v1/me", headers=bearer(old)).status_code == 401
    me = client.get("/api/v1/me", headers=bearer(login_token(client, "alice", temporary))).json()
    assert me["must_change_password"] is True and me["two_factor_enabled"] is False
    assert me["two_factor_disabled_at"] is not None and me["password_reset_at"] is not None
    entries = audit(client)
    assert (
        "user.password_reset",
        None,
        {"by": "container command", "sessions_revoked": 1},
    ) in entries
    assert (
        "user.two_factor_disabled", None, {"by": "container command", "was_enabled": True}
    ) in entries  # fmt: skip


def test_the_flag_on_a_user_without_two_factor_leaves_no_notice(
    client: TestClient, alice, settings: Settings, fast_passwords
) -> None:
    add_user(client, "bob")

    temporary = reset_password(settings, "bob", disable_2fa=True, passwords=fast_passwords)

    me = client.get("/api/v1/me", headers=bearer(login_token(client, "bob", temporary))).json()
    assert me["two_factor_disabled_at"] is None
    assert (
        "user.two_factor_disabled", None, {"by": "container command", "was_enabled": False}
    ) in audit(client)  # fmt: skip
    assert rows(client, TwoFactor) == 1  # alice's


def test_a_locked_out_administrator_gets_back_in_and_enrols_again(
    tmp_path: Path, fast_passwords, clock
) -> None:
    """Lost phone and lost recovery codes, the only administrator, the obligation on: the
    container command, then a new password, then the enrolment before anything else."""
    settings = Settings(data_dir=tmp_path / "data", require_2fa="admins")
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        create_admin(client)
        enable_two_factor(client, "admin")
        client.cookies.clear()
        locked = client.post(LOGIN, headers=CSRF, json={"username": "admin", "password": PASSWORD})
        assert locked.json()["two_factor_required"] is True  # and no code to give
        client.cookies.clear()

        temporary = reset_password(settings, "admin", disable_2fa=True, passwords=fast_passwords)

        login = client.post(LOGIN, headers=CSRF, json={"username": "admin", "password": temporary})
        assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
        assert client.get(USERS).json()["code"] == "PASSWORD_CHANGE_REQUIRED"
        new = "a brand new password"
        changed = client.post(
            "/api/v1/me/password", headers=CSRF,
            json={"current_password": temporary, "new_password": new},
        )  # fmt: skip
        assert changed.json()["two_factor_setup_required"] is True
        assert changed.json()["two_factor_disabled_at"] is not None  # the notice, on the enrolment
        assert client.get(USERS).json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"

        secret = client.post(
            "/api/v1/me/two-factor/setup", headers=CSRF, json={"password": new}
        ).json()["secret"]
        confirmed = client.post(
            "/api/v1/me/two-factor/confirm", headers=CSRF, json={"code": code_now(client, secret)}
        )
        assert confirmed.json()["user"]["two_factor_disabled_at"] is None
        assert client.get(USERS).status_code == 200


def test_the_command_line_takes_the_flag_and_says_what_it_did(
    monkeypatch, tmp_path: Path, capsys, fast_passwords
) -> None:
    monkeypatch.setenv("PT_DATA_DIR", str(tmp_path / "data"))
    settings = Settings()
    with TestClient(create_app(settings, passwords=fast_passwords), base_url="https://t") as client:
        create_admin(client)
        secret, codes = enable_two_factor(client, "admin")
    capsys.readouterr()

    assert main(["reset-password", "admin"]) == 0
    kept = capsys.readouterr().out
    assert "Two-factor authentication, if it is on, stays" in kept and "--disable-2fa" in kept

    assert main(["reset-password", "admin", "--disable-2fa"]) == 0
    removed = capsys.readouterr().out
    assert "Temporary password for admin: " in removed
    assert "Two-factor authentication was removed, if it was on" in removed
    for text in (kept, removed):
        assert secret not in text and not any(code in text for code in codes)
    with TestClient(create_app(settings, passwords=fast_passwords), base_url="https://t") as client:
        assert rows(client, TwoFactor) == 0 and rows(client, UserSession) == 0


# --- the Administrator gives the password and a code again (review finding 1) -----------------


@pytest.fixture
def admin_secret(client: TestClient, alice) -> str:
    """The administrator has two-factor authentication too, and alice a session."""
    return enable_two_factor(client, "admin")[0]


def unchanged(client: TestClient) -> bool:
    """Alice still has her two-factor authentication, her password and no notice."""
    with Session(client.app.state.engine) as db:
        who = users.get_by_username(db, "alice")
        return (
            two_factor.is_enabled(db, who.id)
            and not who.must_change_password
            and who.two_factor_disabled_at is None
            and who.password_reset_at is None
        )


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_a_wrong_password_stops_the_administrator_s_action(
    client: TestClient, admin_secret, action
) -> None:
    response = action(
        client, "alice", password="not the password", code=code_now(client, admin_secret)
    )

    assert (response.status_code, response.json()["code"]) == (422, "CURRENT_PASSWORD_WRONG")
    assert unchanged(client) and "temporary_password" not in response.json()
    assert client.get("/api/v1/me").status_code == 200  # a 422, the session is not over
    assert ("user.step_up_password_failed", user_id(client, "admin"), None) in audit(client)


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_with_two_factor_the_administrator_must_give_a_code(
    client: TestClient, admin_secret, action
) -> None:
    missing = action(client, "alice")
    wrong = action(client, "alice", code=wrong_code(client))

    assert (missing.status_code, missing.json()["code"]) == (422, "TWO_FACTOR_CODE_REQUIRED")
    assert (wrong.status_code, wrong.json()["code"]) == (422, "TWO_FACTOR_CODE_WRONG")
    assert unchanged(client)
    assert ("auth.two_factor_failed", user_id(client, "admin"), None) in audit(client)


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_the_password_and_a_code_let_the_action_through(
    client: TestClient, admin_secret, action
) -> None:
    response = action(client, "alice", code=code_now(client, admin_secret))

    assert response.status_code == 200 and not unchanged(client)


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_a_recovery_code_of_the_administrator_also_counts_once(
    client: TestClient, alice, action
) -> None:
    _, codes = enable_two_factor(client, "admin")
    add_user(client, "bob")

    first = action(client, "alice", recovery_code=codes[0])
    again = action(client, "bob", recovery_code=codes[0])

    assert first.status_code == 200
    assert (again.status_code, again.json()["code"]) == (422, "TWO_FACTOR_CODE_WRONG")


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_the_code_must_be_the_administrator_s_not_the_user_s(
    client: TestClient, admin_secret, alice, action
) -> None:
    alice_secret, alice_codes = alice

    by_code = action(client, "alice", code=code_now(client, alice_secret))
    by_recovery = action(client, "alice", recovery_code=alice_codes[0])

    assert [r.json()["code"] for r in (by_code, by_recovery)] == ["TWO_FACTOR_CODE_WRONG"] * 2
    assert unchanged(client)


@pytest.mark.parametrize("action", [disable_2fa, reset])
def test_without_two_factor_the_password_alone_is_enough_and_a_code_is_ignored(
    client: TestClient, alice, action
) -> None:
    """Only with `PT_REQUIRE_2FA=off`: the administrator has nothing else to give."""
    assert action(client, "alice", code="123456").status_code == 200


@pytest.mark.parametrize("action", [disable_2fa, reset])
@pytest.mark.parametrize(
    "body", [{}, {"code": "123456", "recovery_code": "abcd-efgh-jkmn-pqrs", "password": PASSWORD}]
)
def test_the_body_is_the_password_and_at_most_one_code(
    client: TestClient, alice, action, body: dict
) -> None:
    path = "disable-2fa" if action is disable_2fa else "reset-password"

    response = client.post(f"{USERS}/{user_id(client, 'alice')}/{path}", headers=CSRF, json=body)

    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert PASSWORD not in response.text and "abcd" not in response.text and unchanged(client)


def test_a_valid_code_is_not_used_up_when_the_action_is_refused(
    client: TestClient, admin_secret
) -> None:
    """The code and the action are one transaction: an unknown user undoes both."""
    code = code_now(client, admin_secret)
    missing = client.post(
        f"{USERS}/00000000-0000-4000-8000-000000000000/reset-password",
        headers=CSRF,
        json={"password": PASSWORD, "code": code},
    )

    assert missing.status_code == 404
    assert reset(client, "alice", code=code).status_code == 200  # the same code still works


def test_the_administrator_s_guesses_share_the_limits_of_the_own_account(
    client: TestClient, admin_secret, clock
) -> None:
    for _ in range(5):
        assert reset(client, "alice", code=wrong_code(client)).status_code == 422

    blocked = disable_2fa(client, "alice", code=code_now(client, admin_secret))
    for _ in range(5):
        assert reset(client, "alice", password="not the password").status_code == 422
    password_blocked = reset(client, "alice", code=code_now(client, admin_secret))

    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert password_blocked.status_code == 429 and unchanged(client)


def test_the_password_is_checked_outside_the_write_lock(client: TestClient, admin_secret) -> None:
    """D-94: no Argon2id check while the database's write lock is held."""
    from sqlalchemy import text

    from portfolio_tracker.db import BEGIN_OPTION

    engine = client.app.state.engine
    passwords = client.app.state.passwords
    original, seen = passwords.verify, []

    def verify(password_hash: str, password: str) -> bool:
        with (
            engine.connect().execution_options(**{BEGIN_OPTION: "IMMEDIATE"}) as other,
            other.begin(),  # would wait and fail if the request held the write lock
        ):
            other.execute(text("UPDATE users SET theme = theme"))
        seen.append(True)
        return original(password_hash, password)

    passwords.verify = verify
    try:
        done = disable_2fa(client, "alice", code=code_now(client, admin_secret))
    finally:
        passwords.verify = original

    assert done.status_code == 200 and seen == [True]


# --- a secret shown but never confirmed (review finding 9) ------------------------------------


def test_removing_a_pending_enrolment_is_recorded_and_ends_nothing(
    client: TestClient, alice
) -> None:
    add_user(client, "bob")
    bob = bearer(login_token(client, "bob"))
    with Session(client.app.state.engine) as db, db.begin():
        two_factor.begin(
            db, users.get_by_username(db, "bob").id, "A" * 32, client.app.state.clock()
        )

    response = disable_2fa(client, "bob")

    assert response.status_code == 200 and rows(client, TwoFactor) == 1  # alice's stays
    assert (
        "user.two_factor_disabled", user_id(client, "admin"),
        {"username": "bob", "by": "administrator", "was_enabled": False,
         "pending_secret_removed": True, "sessions_revoked": 0},
    ) in audit(client)  # fmt: skip
    me = client.get("/api/v1/me", headers=bob)  # the session stays, and there is no notice
    assert me.status_code == 200 and me.json()["two_factor_disabled_at"] is None
