"""A user's own two-factor authentication (D-174): enrol, disable, new recovery codes, the
notice. Invented users, passwords, secrets and codes only."""

import base64
import json
import logging
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

from portfolio_tracker.api.deps import SESSION_COOKIE
from portfolio_tracker.auth import recovery, totp
from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import AuditEntry, RecoveryCode, TwoFactor, UserSession
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import two_factor, users

ME = "/api/v1/me"
TWO_FACTOR = "/api/v1/me/two-factor"


def post(client: TestClient, path: str, body: dict | None = None, **kwargs):
    return client.post(f"{TWO_FACTOR}{path}", headers=kwargs.pop("headers", CSRF), json=body)


def setup(client: TestClient, password: str = PASSWORD):
    return post(client, "/setup", {"password": password})


def enrol(client: TestClient) -> tuple[str, list[str]]:
    """Enrol the logged-in user through the API. Returns the secret and the recovery codes."""
    secret = setup(client).json()["secret"]
    confirmed = post(client, "/confirm", {"code": code_now(client, secret)})
    assert confirmed.status_code == 200, confirmed.text
    return secret, confirmed.json()["recovery_codes"]


def row_of(client: TestClient, username: str = "admin") -> TwoFactor | None:
    with Session(client.app.state.engine) as db:
        return two_factor.get(db, users.get_by_username(db, username).id)


def sessions_of(client: TestClient, username: str = "admin") -> int:
    with Session(client.app.state.engine) as db:
        user = users.get_by_username(db, username)
        return len(db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all())


def audit(client: TestClient) -> list[tuple[str, dict | None]]:
    with Session(client.app.state.engine) as db:
        rows = db.scalars(select(AuditEntry).order_by(AuditEntry.at, AuditEntry.id))
        return [(row.action, row.after) for row in rows]


@pytest.fixture
def user(client: TestClient) -> TestClient:
    """The administrator of a new instance, logged in with the cookie; the obligation is off
    (the tests' default), so the administrator is like any user here."""
    create_admin(client)
    return client


# --- /me ----------------------------------------------------------------------------------


def test_me_says_where_two_factor_stands(user: TestClient) -> None:
    before = user.get(ME).json()
    assert (before["two_factor_enabled"], before["recovery_codes_left"]) == (False, None)
    assert before["two_factor_setup_required"] is False and before["two_factor_disabled_at"] is None

    setup(user)
    assert user.get(ME).json()["two_factor_enabled"] is False  # pending is not on

    enrol(user)
    after = user.get(ME).json()
    assert (after["two_factor_enabled"], after["recovery_codes_left"]) == (True, 10)


def test_me_never_carries_the_secret_or_a_code(user: TestClient) -> None:
    secret, codes = enrol(user)

    text = user.get(ME).text + user.get(f"{ME}/sessions").text

    assert secret not in text and not any(code in text for code in codes)


# --- setup ----------------------------------------------------------------------------------


def test_setup_asks_for_the_password_and_shows_the_secret_once(user: TestClient, clock) -> None:
    response = setup(user)

    assert response.status_code == 200
    body = response.json()
    assert len(base64.b32decode(body["secret"])) == 20
    assert body["otpauth_uri"] == totp.provisioning_uri(body["secret"], "admin")
    assert body["qr"] == totp.qr_matrix(body["otpauth_uri"])
    assert (body["digits"], body["period"]) == (6, 30)
    assert body["server_time"].startswith(clock.now.strftime("%Y-%m-%dT%H:%M:%S"))
    row = row_of(user)
    assert row.secret == body["secret"] and row.confirmed_at is None
    assert ("user.two_factor_setup_started", None) in audit(user)


def test_setup_with_a_wrong_password_shows_nothing(user: TestClient) -> None:
    response = setup(user, "not the password")

    assert (response.status_code, response.json()["code"]) == (422, "CURRENT_PASSWORD_WRONG")
    assert "secret" not in response.json() and row_of(user) is None
    assert user.get(ME).status_code == 200  # the session is still there
    assert ("user.two_factor_password_failed", None) in audit(user)


def test_password_guesses_at_setup_share_the_password_change_s_limit(user: TestClient) -> None:
    for _ in range(5):
        assert setup(user, "not the password").status_code == 422

    blocked = setup(user)
    change = user.post(
        f"{ME}/password", headers=CSRF,
        json={"current_password": PASSWORD, "new_password": "another password!"},
    )  # fmt: skip

    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert blocked.headers["Retry-After"] == "60" and change.status_code == 429


def test_starting_again_replaces_a_secret_never_confirmed(user: TestClient) -> None:
    first = setup(user).json()["secret"]
    second = setup(user).json()["secret"]

    assert first != second and row_of(user).secret == second
    wrong = post(user, "/confirm", {"code": code_now(user, first)})
    assert wrong.json()["code"] == "TWO_FACTOR_CODE_WRONG"
    assert post(user, "/confirm", {"code": code_now(user, second)}).status_code == 200


def test_setup_is_refused_while_two_factor_is_on(user: TestClient) -> None:
    secret, _ = enrol(user)

    response = setup(user)

    assert (response.status_code, response.json()["code"]) == (409, "TWO_FACTOR_ALREADY_ENABLED")
    assert row_of(user).secret == secret


@pytest.mark.parametrize("path", ["/setup", "/confirm", "/disable", "/recovery-codes",
                                  "/notice/dismiss"])  # fmt: skip
def test_every_change_needs_the_csrf_header_with_a_cookie(user: TestClient, path: str) -> None:
    response = user.post(f"{TWO_FACTOR}{path}", json={"password": PASSWORD, "code": "000000"})

    assert (response.status_code, response.json()["code"]) == (403, "CSRF_CHECK_FAILED")


# --- confirm --------------------------------------------------------------------------------


def test_the_first_code_turns_it_on_and_gives_ten_recovery_codes_once(user: TestClient) -> None:
    secret = setup(user).json()["secret"]

    response = post(user, "/confirm", {"code": code_now(user, secret)})

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["two_factor_enabled"] is True and body["user"]["recovery_codes_left"] == 10
    codes = body["recovery_codes"]
    assert len(set(codes)) == 10 and all(recovery.normalize(code) for code in codes)
    with Session(user.app.state.engine) as db:
        stored = sorted(db.scalars(select(RecoveryCode.code_hash)))
    assert stored == sorted(recovery.hash_code(code) for code in codes)
    assert row_of(user).confirmed_at is not None
    assert ("user.two_factor_enabled", {"other_sessions_revoked": 0}) in audit(user)


def test_the_code_that_confirmed_cannot_be_used_again_at_login(user: TestClient) -> None:
    secret = setup(user).json()["secret"]
    code = code_now(user, secret)
    post(user, "/confirm", {"code": code})
    user.cookies.clear()

    user.post("/api/v1/auth/login", headers=CSRF, json={"username": "admin", "password": PASSWORD})
    reused = user.post("/api/v1/auth/login/two-factor", headers=CSRF, json={"code": code})

    assert (reused.status_code, reused.json()["code"]) == (401, "TWO_FACTOR_CODE_WRONG")


def test_a_wrong_code_does_not_turn_it_on(user: TestClient) -> None:
    setup(user)

    response = post(user, "/confirm", {"code": wrong_code(user)})

    assert (response.status_code, response.json()["code"]) == (422, "TWO_FACTOR_CODE_WRONG")
    assert row_of(user).confirmed_at is None
    assert user.get(ME).json()["two_factor_enabled"] is False
    assert ("auth.two_factor_failed", None) in audit(user)


def test_confirm_without_a_setup_is_refused(user: TestClient) -> None:
    response = post(user, "/confirm", {"code": wrong_code(user)})

    assert (response.status_code, response.json()["code"]) == (409, "TWO_FACTOR_NOT_STARTED")


def test_confirm_is_refused_when_already_on(user: TestClient) -> None:
    secret, _ = enrol(user)

    response = post(user, "/confirm", {"code": code_now(user, secret, 1)})

    assert (response.status_code, response.json()["code"]) == (409, "TWO_FACTOR_ALREADY_ENABLED")
    assert user.get(ME).json()["recovery_codes_left"] == 10  # the codes were not replaced


def test_five_wrong_codes_block_the_confirmation(user: TestClient, clock) -> None:
    secret = setup(user).json()["secret"]
    for _ in range(5):
        assert post(user, "/confirm", {"code": wrong_code(user)}).status_code == 422

    blocked = post(user, "/confirm", {"code": code_now(user, secret)})

    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert blocked.json()["retry_after"] == 60 and row_of(user).confirmed_at is None
    clock.advance(seconds=60)
    assert post(user, "/confirm", {"code": code_now(user, secret)}).status_code == 200


def test_enabling_ends_the_other_sessions_and_renews_this_one(user: TestClient) -> None:
    other = login_token(user, "admin")
    old_cookie = user.cookies.get(SESSION_COOKIE)
    secret = setup(user).json()["secret"]

    response = post(user, "/confirm", {"code": code_now(user, secret)})

    assert response.json()["token"] is None  # the web app gets the cookie only
    assert sessions_of(user) == 1
    assert user.get(ME, headers=bearer(other)).status_code == 401
    new_cookie = user.cookies.get(SESSION_COOKIE)
    assert new_cookie != old_cookie and user.get(ME).status_code == 200
    user.cookies.clear()
    user.cookies.set(SESSION_COOKIE, old_cookie)
    assert user.get(ME).status_code == 401  # the token seen before no longer works


def test_a_bearer_client_gets_its_new_token_in_the_answer(user: TestClient) -> None:
    token = login_token(user, "admin")
    user.cookies.clear()
    headers = bearer(token)
    secret = post(user, "/setup", {"password": PASSWORD}, headers=headers).json()["secret"]

    response = post(user, "/confirm", {"code": code_now(user, secret)}, headers=headers)

    new = response.json()["token"]
    assert new and new != token and "set-cookie" not in response.headers
    assert user.get(ME, headers=bearer(new)).status_code == 200
    assert user.get(ME, headers=headers).status_code == 401


# --- disable --------------------------------------------------------------------------------


def body(password: str = PASSWORD, **factor) -> dict:
    return {"password": password, **factor}


def test_disabling_needs_the_password_and_a_code(user: TestClient) -> None:
    secret, _ = enrol(user)

    wrong_password = post(
        user, "/disable", body("not the password", code=code_now(user, secret, 1))
    )
    bad_code = post(user, "/disable", body(code=wrong_code(user)))

    assert (wrong_password.status_code, wrong_password.json()["code"]) == (
        422, "CURRENT_PASSWORD_WRONG",
    )  # fmt: skip
    assert (bad_code.status_code, bad_code.json()["code"]) == (422, "TWO_FACTOR_CODE_WRONG")
    assert user.get(ME).json()["two_factor_enabled"] is True
    assert row_of(user).last_step == totp.step_of(user.app.state.clock())  # no step was used


@pytest.mark.parametrize("missing", [{}, {"code": "000000", "recovery_code": "x"}])
def test_disabling_takes_exactly_one_code(user: TestClient, missing: dict) -> None:
    enrol(user)

    response = post(user, "/disable", body(**missing))

    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert PASSWORD not in response.text


def test_disabling_deletes_the_secret_and_the_recovery_codes(user: TestClient) -> None:
    secret, _ = enrol(user)

    response = post(user, "/disable", body(code=code_now(user, secret, 1)))

    assert response.status_code == 200
    out = response.json()["user"]
    assert (out["two_factor_enabled"], out["recovery_codes_left"]) == (False, None)
    assert out["two_factor_disabled_at"] is None  # no notice for one's own choice
    assert row_of(user) is None
    with Session(user.app.state.engine) as db:
        assert db.scalars(select(RecoveryCode)).all() == []
    assert ("user.two_factor_disabled", {"by": "user", "other_sessions_revoked": 0}) in audit(user)
    user.cookies.clear()
    login = user.post(
        "/api/v1/auth/login", headers=CSRF, json={"username": "admin", "password": PASSWORD}
    )
    assert login.json()["user"]["username"] == "admin"  # one step again


def test_a_recovery_code_also_disables(user: TestClient) -> None:
    _, codes = enrol(user)

    assert post(user, "/disable", body(recovery_code=codes[4])).status_code == 200
    assert row_of(user) is None


def test_disabling_ends_the_other_sessions_and_renews_this_one(user: TestClient) -> None:
    secret, _ = enrol(user)
    with Session(user.app.state.engine) as db, db.begin():  # another device, still logged in
        admin = users.get_by_username(db, "admin")
        from portfolio_tracker.auth import sessions

        other, _ = sessions.create_session(
            db, admin, user.app.state.clock(), user.app.state.session_policy, None
        )
    old_cookie = user.cookies.get(SESSION_COOKIE)

    post(user, "/disable", body(code=code_now(user, secret, 1)))

    assert sessions_of(user) == 1 and user.get(ME, headers=bearer(other)).status_code == 401
    assert user.cookies.get(SESSION_COOKIE) != old_cookie and user.get(ME).status_code == 200


def test_disabling_what_is_off_is_refused(user: TestClient) -> None:
    response = post(user, "/disable", body(code=wrong_code(user)))

    assert (response.status_code, response.json()["code"]) == (409, "TWO_FACTOR_NOT_ENABLED")


def test_code_guesses_when_disabling_are_limited_like_the_login_s(user: TestClient, clock) -> None:
    secret, _ = enrol(user)
    for _ in range(5):
        assert post(user, "/disable", body(code=wrong_code(user))).status_code == 422

    blocked = post(user, "/disable", body(code=code_now(user, secret, 1)))
    codes = post(user, "/recovery-codes", body(code=code_now(user, secret, 1)))

    assert (blocked.status_code, codes.status_code) == (429, 429)
    assert user.get(ME).json()["two_factor_enabled"] is True
    user.cookies.clear()
    user.post("/api/v1/auth/login", headers=CSRF, json={"username": "admin", "password": PASSWORD})
    login = user.post(
        "/api/v1/auth/login/two-factor", headers=CSRF, json={"code": code_now(user, secret, 1)}
    )
    assert login.status_code == 429  # one count for the user's codes, wherever they are typed


# --- new recovery codes -----------------------------------------------------------------------


def test_new_recovery_codes_replace_the_old_ones(user: TestClient) -> None:
    secret, old = enrol(user)

    response = post(user, "/recovery-codes", body(code=code_now(user, secret, 1)))

    assert response.status_code == 200
    new = response.json()["recovery_codes"]
    assert len(set(new)) == 10 and not set(new) & set(old)
    assert response.json()["user"]["recovery_codes_left"] == 10
    assert ("user.recovery_codes_replaced", {"other_sessions_revoked": 0}) in audit(user)
    assert post(user, "/disable", body(recovery_code=old[0])).json()["code"] == (
        "TWO_FACTOR_CODE_WRONG"
    )
    assert post(user, "/disable", body(recovery_code=new[0])).status_code == 200


def test_a_recovery_code_used_to_get_new_ones_counts_as_used(user: TestClient) -> None:
    _, old = enrol(user)

    assert post(user, "/recovery-codes", body(recovery_code=old[0])).status_code == 200
    again = post(user, "/recovery-codes", body(recovery_code=old[0]))

    assert again.json()["code"] == "TWO_FACTOR_CODE_WRONG"


def test_new_recovery_codes_need_the_password_and_a_code(user: TestClient) -> None:
    secret, _ = enrol(user)
    with Session(user.app.state.engine) as db:
        before = sorted(db.scalars(select(RecoveryCode.code_hash)))

    answers = [
        post(user, "/recovery-codes", body("not the password", code=code_now(user, secret, 1))),
        post(user, "/recovery-codes", body(code=wrong_code(user))),
    ]

    assert [a.json()["code"] for a in answers] == [
        "CURRENT_PASSWORD_WRONG",
        "TWO_FACTOR_CODE_WRONG",
    ]
    assert all("recovery_codes" not in a.json() for a in answers)
    with Session(user.app.state.engine) as db:
        assert sorted(db.scalars(select(RecoveryCode.code_hash))) == before


def test_new_recovery_codes_end_the_other_sessions(user: TestClient) -> None:
    secret, _ = enrol(user)
    old_cookie = user.cookies.get(SESSION_COOKIE)

    post(user, "/recovery-codes", body(code=code_now(user, secret, 1)))

    assert sessions_of(user) == 1 and user.cookies.get(SESSION_COOKIE) != old_cookie


def test_new_recovery_codes_without_two_factor_are_refused(user: TestClient) -> None:
    response = post(user, "/recovery-codes", body(code=wrong_code(user)))

    assert (response.status_code, response.json()["code"]) == (409, "TWO_FACTOR_NOT_ENABLED")


# --- the notice -------------------------------------------------------------------------------


def _removed_by_someone_else(client: TestClient, username: str = "admin") -> None:
    with Session(client.app.state.engine) as db, db.begin():
        who = users.get_by_username(db, username)
        two_factor.remove(db, who.id)
        who.two_factor_disabled_at = client.app.state.clock()


def test_the_notice_stays_until_dismissed(user: TestClient, clock) -> None:
    enrol(user)
    _removed_by_someone_else(user)
    assert user.get(ME).json()["two_factor_disabled_at"].startswith("2026-01-15")
    clock.advance(days=3)
    assert user.get(ME).json()["two_factor_disabled_at"] is not None  # still there

    dismissed = post(user, "/notice/dismiss")

    assert dismissed.status_code == 200 and dismissed.json()["two_factor_disabled_at"] is None
    assert user.get(ME).json()["two_factor_disabled_at"] is None
    assert post(user, "/notice/dismiss").status_code == 200  # nothing to dismiss: harmless
    assert [a for a, _ in audit(user)].count("user.two_factor_notice_dismissed") == 1


def test_the_notice_ends_when_two_factor_is_enabled_again(user: TestClient) -> None:
    enrol(user)
    _removed_by_someone_else(user)

    secret = setup(user).json()["secret"]
    confirmed = post(user, "/confirm", {"code": code_now(user, secret, 1)})

    assert confirmed.json()["user"]["two_factor_disabled_at"] is None


def test_one_user_s_changes_never_touch_another_s(user: TestClient) -> None:
    add_user(user, "bob")
    bob_secret, bob_codes = enable_two_factor(user, "bob")
    secret, _ = enrol(user)

    assert post(user, "/disable", body(code=code_now(user, bob_secret, 1))).status_code == 422
    assert post(user, "/disable", body(recovery_code=bob_codes[0])).status_code == 422
    assert post(user, "/disable", body(code=code_now(user, secret, 1))).status_code == 200

    assert row_of(user, "bob").secret == bob_secret
    with Session(user.app.state.engine) as db:
        assert two_factor.recovery_codes_left(db, users.get_by_username(db, "bob").id) == 10


# --- the obligation for administrators ----------------------------------------------------------


@pytest.fixture
def obliged(tmp_path: Path, fast_passwords, clock):
    """An app with the default `PT_REQUIRE_2FA=admins`."""
    settings = Settings(data_dir=tmp_path / "obliged", require_2fa="admins")
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_the_default_obliges_nobody_until_the_enrolment_screen_exists(
    tmp_path: Path, monkeypatch
) -> None:
    """D-174: `off` until the pull request that adds the screens turns it to `admins`; an
    administrator stopped with no screen to enrol on would be locked out of the web app."""
    monkeypatch.delenv("PT_REQUIRE_2FA")

    assert Settings(data_dir=tmp_path).require_2fa == "off"


@pytest.mark.parametrize("value", ["on", "all", "admin", "", "true"])
def test_an_unknown_value_stops_the_start(tmp_path: Path, monkeypatch, value: str) -> None:
    monkeypatch.setenv("PT_REQUIRE_2FA", value)

    with pytest.raises(ValueError, match="require_2fa"):
        Settings(data_dir=tmp_path)


def test_the_first_administrator_must_enrol_before_anything_else(obliged: TestClient) -> None:
    created = create_admin(obliged)

    assert created["user"]["two_factor_setup_required"] is True
    assert obliged.get(ME).json()["two_factor_setup_required"] is True
    for path in ("/accounts", "/admin/users", "/portfolio/summary", "/me/sessions"):
        refused = obliged.get(f"/api/v1{path}")
        assert (refused.status_code, refused.json()["code"]) == (403, "TWO_FACTOR_SETUP_REQUIRED")

    secret, _ = enrol(obliged)

    assert obliged.get(ME).json()["two_factor_setup_required"] is False
    assert obliged.get("/api/v1/admin/users").status_code == 200
    assert secret


def test_an_open_session_is_stopped_at_its_next_request(obliged: TestClient) -> None:
    """Not only at login: a session open before the upgrade, or before a promotion."""
    create_admin(obliged)
    enrol(obliged)
    assert obliged.get("/api/v1/accounts").status_code == 200

    _removed_by_someone_else(obliged)

    refused = obliged.get("/api/v1/accounts")
    assert (refused.status_code, refused.json()["code"]) == (403, "TWO_FACTOR_SETUP_REQUIRED")


def test_an_investor_is_not_obliged_and_a_promotion_is_followed(obliged: TestClient) -> None:
    create_admin(obliged)
    add_user(obliged, "bob")
    bob = bearer(login_token(obliged, "bob"))
    assert obliged.get(ME, headers=bob).json()["two_factor_setup_required"] is False
    assert obliged.get("/api/v1/accounts", headers=bob).status_code == 200

    with Session(obliged.app.state.engine) as db, db.begin():
        users.get_by_username(db, "bob").role = "ADMINISTRATOR"

    assert obliged.get(ME, headers=bob).json()["two_factor_setup_required"] is True
    assert (
        obliged.get("/api/v1/accounts", headers=bob).json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"
    )


def test_an_obliged_administrator_who_disables_is_taken_back_to_the_enrolment(
    obliged: TestClient,
) -> None:
    create_admin(obliged)
    secret, _ = enrol(obliged)

    disabled = post(obliged, "/disable", body(code=code_now(obliged, secret, 1)))

    assert disabled.status_code == 200
    assert disabled.json()["user"]["two_factor_setup_required"] is True
    assert obliged.get("/api/v1/accounts").json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"
    enrol(obliged)  # a new phone
    assert obliged.get("/api/v1/accounts").status_code == 200


def test_a_temporary_password_is_changed_before_the_enrolment(obliged: TestClient) -> None:
    create_admin(obliged)
    with Session(obliged.app.state.engine) as db, db.begin():
        users.get_by_username(db, "admin").must_change_password = True

    refused = setup(obliged)

    assert (refused.status_code, refused.json()["code"]) == (403, "PASSWORD_CHANGE_REQUIRED")
    changed = obliged.post(
        f"{ME}/password", headers=CSRF,
        json={"current_password": PASSWORD, "new_password": "another password!"},
    )  # fmt: skip
    assert changed.status_code == 200 and changed.json()["two_factor_setup_required"] is True
    assert setup(obliged, "another password!").status_code == 200


def test_the_start_log_warns_when_the_obligation_is_off(
    tmp_path: Path, fast_passwords, capsys: pytest.CaptureFixture
) -> None:
    """The app logs JSON to standard output (its own handler replaces pytest's)."""

    def started(value: str) -> list[dict]:
        settings = Settings(data_dir=tmp_path / value, require_2fa=value)
        with TestClient(create_app(settings, passwords=fast_passwords)):
            pass
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
        return [line for line in lines if "PT_REQUIRE_2FA" in line["message"]]

    [warning] = started("off")
    assert warning["level"] == "WARNING"
    assert started("admins") == []


# --- nothing leaks ------------------------------------------------------------------------------


def test_no_secret_or_code_reaches_the_logs_the_errors_or_the_audit_log(
    user: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        secret = setup(user).json()["secret"]
        bad = wrong_code(user)
        errors = [post(user, "/confirm", {"code": bad})]
        first = code_now(user, secret)
        codes = post(user, "/confirm", {"code": first}).json()["recovery_codes"]
        errors.append(post(user, "/recovery-codes", body(code=bad)))
        errors.append(post(user, "/recovery-codes", body("a wrong password", code=bad)))
        second = code_now(user, secret, 1)
        new = post(user, "/recovery-codes", body(code=second)).json()["recovery_codes"]
        errors.append(post(user, "/disable", body(recovery_code="zzzz-zzzz-zzzz-zzzz")))
        assert post(user, "/disable", body(recovery_code=new[0])).status_code == 200

    logged = "\n".join(f"{record.getMessage()} {vars(record)}" for record in caplog.records)
    stored = repr(audit(user))
    answers = "".join(error.text for error in errors)
    assert "code check: wrong code" in logged and "wrong current password" in logged  # captured
    values = [secret, first, second, bad, "zzzz-zzzz-zzzz-zzzz",
              "a wrong password", PASSWORD, *codes, *new]  # fmt: skip
    for value in values:
        assert value not in logged, value
        assert value not in stored, value
        assert value not in answers, value
