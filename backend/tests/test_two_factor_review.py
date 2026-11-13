"""The fixes after the independent security review of two-factor authentication (D-174):
the memory of code guesses, secrets never confirmed, the audit of a verified password,
`Cache-Control: no-store` on the API, nothing secret in a `repr`, proofs dropped when
two-factor authentication goes, the address limit shared by passwords and codes, and a wrong
code that really undoes its transaction. Invented users, secrets and codes only."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from helpers import (
    CSRF,
    PASSWORD,
    add_user,
    code_now,
    create_admin,
    details,
    enable_two_factor,
    wrong_code,
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

from portfolio_tracker.api import reauth
from portfolio_tracker.api.routes.admin import StepUpIn, TemporaryPasswordOut
from portfolio_tracker.api.routes.auth import LoginStepOut, SecondStepIn
from portfolio_tracker.api.routes.two_factor import (
    ChangedOut,
    CodeIn,
    PasswordAndCodeIn,
    PasswordIn,
    RecoveryCodesOut,
    SetupOut,
)
from portfolio_tracker.auth import totp
from portfolio_tracker.auth.challenge import LoginChallenges
from portfolio_tracker.auth.throttle import (
    LoginThrottle,
    login_keys,
    password_change_keys,
    totp_keys,
)
from portfolio_tracker.cli import reset_password
from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import AuditEntry, TwoFactor
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import two_factor, users
from portfolio_tracker.services.market_startup import housekeeping

LOGIN = "/api/v1/auth/login"
SECOND = "/api/v1/auth/login/two-factor"
TWO_FACTOR = "/api/v1/me/two-factor"


def actions(client: TestClient) -> list[str]:
    with Session(client.app.state.engine) as db:
        return list(db.scalars(select(AuditEntry.action)))


def password_step(client: TestClient, username: str = "alice", **extra):
    return client.post(
        LOGIN, headers=CSRF, json={"username": username, "password": PASSWORD, **extra}
    )


def code_step(client: TestClient, **body):
    return client.post(SECOND, headers=CSRF, json=body)


@pytest.fixture
def alice(client: TestClient) -> tuple[str, list[str]]:
    """An administrator exists (logged out again) and alice has two-factor authentication."""
    create_admin(client)
    client.post("/api/v1/auth/logout", headers=CSRF)
    client.cookies.clear()
    add_user(client, "alice")
    return enable_two_factor(client, "alice")


# --- finding 2: code guesses are remembered for a day -----------------------------------------


def test_code_guesses_are_remembered_for_a_day_passwords_for_half_an_hour() -> None:
    throttle = LoginThrottle()
    user = uuid4()
    t0 = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)

    def fail(keys: list[str], at) -> bool:
        with throttle.attempt(keys, at) as attempt:
            return not attempt.refused

    for kind_keys in (totp_keys(user), password_change_keys(user), login_keys("anna", None)):
        for _ in range(4):
            assert fail(kind_keys, t0)
    later = t0 + timedelta(minutes=31)

    # Half an hour later the password counts are forgotten, the code count is not.
    for _ in range(5):
        assert fail(password_change_keys(user), later)
        assert fail(login_keys("anna", None), later)
    assert fail(totp_keys(user), later)  # the fifth wrong code
    assert not fail(totp_keys(user), later)  # blocked

    # After a day without wrong codes the count is forgotten too.
    day_after = later + timedelta(hours=24)
    for _ in range(5):
        assert fail(totp_keys(user), day_after)


def test_four_wrong_codes_every_half_hour_end_in_a_block(client: TestClient, alice, clock) -> None:
    """With the 30-minute memory an attacker who knows the password could try 5 codes every
    half hour for ever and never be blocked."""
    secret, _ = alice
    for _ in range(4):
        password_step(client)
        assert code_step(client, code=wrong_code(client)).status_code == 401
    clock.advance(minutes=31)
    password_step(client)
    assert code_step(client, code=wrong_code(client)).status_code == 401  # the fifth

    blocked = code_step(client, code=code_now(client, secret))

    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")


def test_the_block_still_doubles_up_to_fifteen_minutes(client: TestClient, alice, clock) -> None:
    waits = []
    for _ in range(5):
        password_step(client)
        code_step(client, code=wrong_code(client))
    for _ in range(6):
        clock.advance(seconds=waits[-1] if waits else 60)
        password_step(client)
        code_step(client, code=wrong_code(client))
        waits.append(code_step(client, code=wrong_code(client)).json()["retry_after"])

    assert waits == [120, 240, 480, 900, 900, 900]


# --- finding 3: a secret shown but never confirmed ---------------------------------------------


@pytest.fixture
def pending(client: TestClient) -> str:
    """The administrator, logged in, was shown a secret and has not confirmed it."""
    create_admin(client)
    shown = client.post(f"{TWO_FACTOR}/setup", headers=CSRF, json={"password": PASSWORD})
    return shown.json()["secret"]


def confirm(client: TestClient, secret: str):
    return client.post(
        f"{TWO_FACTOR}/confirm", headers=CSRF, json={"code": code_now(client, secret)}
    )


def test_a_secret_can_be_confirmed_for_fifteen_minutes(client: TestClient, pending, clock) -> None:
    clock.advance(minutes=14, seconds=59)

    assert confirm(client, pending).status_code == 200


@pytest.mark.parametrize("later", [{"minutes": 15}, {"hours": 3}, {"days": 12}])
def test_an_older_secret_is_refused_and_the_setup_starts_again(
    client: TestClient, pending, clock, later: dict
) -> None:
    clock.advance(**later)
    if client.get("/api/v1/me").status_code == 401:  # the session itself ended meanwhile
        assert password_step(client, "admin").json()["user"]["two_factor_enabled"] is False

    refused = confirm(client, pending)

    assert (refused.status_code, refused.json()["code"]) == (409, "TWO_FACTOR_NOT_STARTED")
    assert client.get("/api/v1/me").json()["two_factor_enabled"] is False
    assert "auth.two_factor_failed" not in actions(client)  # not a guess
    again = client.post(f"{TWO_FACTOR}/setup", headers=CSRF, json={"password": PASSWORD})
    assert again.json()["secret"] != pending
    assert confirm(client, again.json()["secret"]).status_code == 200


def test_a_confirmed_secret_never_expires(client: TestClient, pending, clock) -> None:
    confirm(client, pending)
    client.cookies.clear()
    clock.advance(days=12)

    password_step(client, "admin")
    assert code_step(client, code=code_now(client, pending)).status_code == 200


def test_the_housekeeping_deletes_secrets_left_unconfirmed_for_a_day(
    client: TestClient, pending, clock
) -> None:
    add_user(client, "alice")
    alice_secret, _ = enable_two_factor(client, "alice")  # confirmed
    add_user(client, "bob")
    clock.advance(hours=23, minutes=59)
    with Session(client.app.state.engine) as db, db.begin():  # shown a moment ago
        two_factor.begin(db, users.get_by_username(db, "bob").id, "B" * 32, clock.now)
    run = housekeeping(client.app.state.engine, client.app.state.session_policy, clock)

    def secrets() -> set[str]:
        with Session(client.app.state.engine) as db:
            return set(db.scalars(select(TwoFactor.secret)))

    run()
    assert secrets() == {pending, alice_secret, "B" * 32}  # not a day yet

    clock.advance(minutes=2)
    run()
    assert secrets() == {alice_secret, "B" * 32}  # the old pending one is gone

    clock.advance(days=12)
    run()
    assert secrets() == {alice_secret}  # a confirmed secret is never touched


# --- finding 4: the verified password of a user with two-factor authentication -----------------


def test_a_right_password_before_the_code_is_in_the_audit_log_only(
    client: TestClient, alice, caplog: pytest.LogCaptureFixture
) -> None:
    add_user(client, "bob")
    with caplog.at_level(logging.DEBUG):
        password_step(client)
        client.cookies.clear()
        password_step(client, "bob")  # no two-factor authentication: nothing extra

    assert actions(client).count("auth.login_password_verified") == 1
    with Session(client.app.state.engine) as db:
        entry = db.scalars(
            select(AuditEntry).where(AuditEntry.action == "auth.login_password_verified")
        ).one()
        assert entry.actor_user_id == users.get_by_username(db, "alice").id
    assert not any("password_verified" in record.getMessage() for record in caplog.records)
    assert not any("password verified" in record.getMessage() for record in caplog.records)


def test_a_wrong_password_records_no_verified_password(client: TestClient, alice) -> None:
    client.post(LOGIN, headers=CSRF, json={"username": "alice", "password": "not the password"})

    assert "auth.login_password_verified" not in actions(client)


# --- finding 6: nothing of the API is kept by a cache ------------------------------------------


@pytest.fixture
def web(tmp_path: Path, fast_passwords, clock) -> Iterator[TestClient]:
    """The app with a built frontend."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)")
    settings = Settings(data_dir=tmp_path / "data", static_dir=static, market_data=False)
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_answers_that_show_something_once_are_never_stored(web: TestClient) -> None:
    def stored(response) -> list[str]:
        return response.headers.get_list("cache-control")

    setup = web.post(
        "/api/v1/setup/admin", headers=CSRF,
        json={"username": "admin", "password": PASSWORD, "language": "en"} | details("admin"),
    )  # fmt: skip
    shown = web.post(f"{TWO_FACTOR}/setup", headers=CSRF, json={"password": PASSWORD})
    secret = shown.json()["secret"]
    codes = web.post(f"{TWO_FACTOR}/confirm", headers=CSRF, json={"code": code_now(web, secret)})
    created = web.post(
        "/api/v1/admin/users", headers=CSRF, json={"username": "marta", **details("marta")}
    )
    reset = web.post(
        f"/api/v1/admin/users/{created.json()['user']['id']}/reset-password", headers=CSRF,
        json={"password": PASSWORD, "code": code_now(web, secret, 1)},
    )  # fmt: skip
    new_codes = web.post(
        f"{TWO_FACTOR}/recovery-codes", headers=CSRF,
        json={"password": PASSWORD, "recovery_code": codes.json()["recovery_codes"][0]},
    )  # fmt: skip
    web.cookies.clear()
    step = web.post(
        LOGIN, headers=CSRF, json={"username": "admin", "password": PASSWORD, "issue_token": True}
    )
    token = web.post(
        SECOND, headers=CSRF,
        json={"recovery_code": new_codes.json()["recovery_codes"][0],
              "challenge": step.json()["challenge"]},
    )  # fmt: skip

    answers = {"setup": setup, "secret": shown, "recovery codes": codes, "user": created,
               "temporary password": reset, "new codes": new_codes, "proof": step,
               "token": token}  # fmt: skip
    assert [r.status_code for r in answers.values()] == [201, 200, 200, 201, 200, 200, 200, 200]
    assert token.json()["token"] and reset.json()["temporary_password"]
    for name, response in answers.items():
        assert stored(response) == ["no-store"], name


def test_every_other_answer_of_the_api_is_not_stored_either(web: TestClient) -> None:
    create_admin(web)
    for path in ("/api/health", "/api/v1/me", "/api/v1/accounts", "/api/v1/nothing-here"):
        assert web.get(path).headers.get_list("cache-control") == ["no-store"], path
    web.cookies.clear()
    refused = web.get("/api/v1/me")
    assert refused.status_code == 401 and refused.headers["cache-control"] == "no-store"
    wrong = web.post(LOGIN, headers=CSRF, json={"username": "admin", "password": "wrong one!!"})
    assert wrong.status_code == 401 and wrong.headers["cache-control"] == "no-store"


def test_the_frontend_and_its_assets_keep_their_own_caching(web: TestClient) -> None:
    asset = web.get("/assets/index-abc123.js")
    page = web.get("/")

    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert page.status_code == 200 and "no-store" not in page.headers.get("cache-control", "")


# --- finding 7: nothing secret in a representation ---------------------------------------------

SECRETS = ["S3CRETSECRETSECRETSECRETSECRET22", "otpauth://totp/x?secret=S3CRET", "101010",
           "tHq4-Rw8e-LmZ2-9Vcx", "the-proof-value", "the-token-value", "the password!",
           "$argon2id$the-hash", "654321"]  # fmt: skip


@pytest.mark.parametrize(
    "value",
    [
        SetupOut(secret=SECRETS[0], otpauth_uri=SECRETS[1], qr=[SECRETS[2]],
                 server_time="2026-01-15T09:00:00Z"),
        LoginStepOut(challenge=SECRETS[4], expires_in=300),
        SecondStepIn(code=SECRETS[8], challenge=SECRETS[4]),
        SecondStepIn(recovery_code=SECRETS[3]),
        CodeIn(code=SECRETS[8]),
        PasswordIn(password=SECRETS[6]),
        PasswordAndCodeIn(password=SECRETS[6], code=SECRETS[8]),
        PasswordAndCodeIn(password=SECRETS[6], recovery_code=SECRETS[3]),
        StepUpIn(password=SECRETS[6], code=SECRETS[8]),
        StepUpIn(password=SECRETS[6], recovery_code=SECRETS[3]),
    ],
    ids=lambda value: type(value).__name__,
)  # fmt: skip
def test_no_model_shows_a_secret_in_its_representation(value) -> None:
    for text in (repr(value), str(value), f"{value!r:>10}"):
        assert not any(secret in text for secret in SECRETS), text


def test_the_answers_with_a_user_show_no_code_or_token(client: TestClient) -> None:
    user = create_admin(client)["user"]
    admin_row = client.get("/api/v1/admin/users").json()[0]
    values = [
        ChangedOut(user=user, token=SECRETS[5]),
        RecoveryCodesOut(user=user, token=SECRETS[5], recovery_codes=[SECRETS[3]]),
        TemporaryPasswordOut(user=admin_row, temporary_password=SECRETS[3]),
    ]

    for value in values:
        assert not any(secret in repr(value) + str(value) for secret in SECRETS), type(value)


def test_a_pending_login_shows_no_password_hash_and_the_store_no_proof() -> None:
    store = LoginChallenges()
    at = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
    proof = store.issue(uuid4(), SECRETS[7], True, at)
    pending_login = store.peek(proof, at)

    assert SECRETS[7] not in repr(pending_login) and SECRETS[7] not in str(pending_login)
    assert pending_login.password_hash == SECRETS[7]  # still there for the second step
    assert proof not in repr(store._entries) and SECRETS[7] not in repr(store._entries)


# --- finding 8: a proof issued before two-factor authentication was removed --------------------


def test_disabling_one_s_own_two_factor_drops_the_pending_logins(client: TestClient, alice) -> None:
    secret, _ = alice
    proof = password_step(client, issue_token=True).json()["challenge"]  # another device waits
    client.cookies.clear()
    password_step(client)
    assert code_step(client, code=code_now(client, secret)).status_code == 200
    assert len(client.app.state.login_challenges) == 1

    disabled = client.post(
        f"{TWO_FACTOR}/disable", headers=CSRF,
        json={"password": PASSWORD, "code": code_now(client, secret, 1)},
    )  # fmt: skip

    assert disabled.status_code == 200 and len(client.app.state.login_challenges) == 0
    late = code_step(client, code=code_now(client, secret, 2), challenge=proof)
    assert (late.status_code, late.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")


def test_a_proof_of_a_user_without_two_factor_any_more_is_over_not_a_wrong_guess(
    client: TestClient, alice, clock
) -> None:
    """Also when the proof is still in the store: removed outside this process (the container
    command), or issued by a request that raced the removal."""
    secret, _ = alice
    proofs = [password_step(client, issue_token=True).json()["challenge"] for _ in range(7)]
    client.cookies.clear()
    with Session(client.app.state.engine) as db, db.begin():
        two_factor.remove(db, users.get_by_username(db, "alice").id)
    before = actions(client)

    late = [code_step(client, code=code_now(client, secret), challenge=proof) for proof in proofs]

    assert {(r.status_code, r.json()["code"]) for r in late} == {(401, "LOGIN_STEP_EXPIRED")}
    assert actions(client) == before  # no `auth.two_factor_failed`
    assert len(client.app.state.login_challenges) == 0
    # Seven would have blocked the user's codes: they were not counted as guesses.
    enable_two_factor(client, "alice")
    password_step(client)
    wrong = code_step(client, code=wrong_code(client))
    assert (wrong.status_code, wrong.json()["attempts_left"]) == (401, 4)


def test_a_proof_issued_before_the_command_s_disable_2fa_does_not_complete_the_login(
    client: TestClient, alice, settings: Settings, fast_passwords
) -> None:
    secret, codes = alice
    proof = password_step(client, issue_token=True).json()["challenge"]
    client.cookies.clear()

    temporary = reset_password(settings, "alice", disable_2fa=True, passwords=fast_passwords)

    for body in ({"code": code_now(client, secret)}, {"recovery_code": codes[0]}):
        late = code_step(client, challenge=proof, **body)
        assert (late.status_code, late.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")
    with Session(client.app.state.engine) as db:
        assert db.scalars(select(AuditEntry.action)).all().count("auth.login") == 0
    again = client.post(LOGIN, headers=CSRF, json={"username": "alice", "password": temporary})
    assert again.json()["user"]["must_change_password"] is True  # one step, the new password


# --- finding 11: the address limit is shared by passwords and codes ----------------------------


def test_wrong_passwords_and_wrong_codes_count_together_for_the_address(
    client: TestClient, alice
) -> None:
    """Each user's own counts are apart (`user:`, `totp:`); the address key is one, 30 in
    all, whatever was wrong."""
    add_user(client, "bob")
    enable_two_factor(client, "bob")
    for n in range(20):  # twenty wrong passwords, each for another name
        wrong = client.post(
            LOGIN, headers=CSRF, json={"username": f"nobody{n}", "password": "not the password"}
        )
        assert wrong.status_code == 401
    for name in ("alice", "bob"):  # ten wrong codes, five for each user
        password_step(client, name)
        for _ in range(5):
            assert code_step(client, code=wrong_code(client)).status_code == 401
        client.cookies.clear()
    add_user(client, "carla")

    blocked = password_step(client, "carla")  # a right password, a name never tried

    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")


def test_twenty_nine_wrong_ones_from_an_address_do_not_block_it(client: TestClient, alice) -> None:
    for n in range(24):
        client.post(
            LOGIN, headers=CSRF, json={"username": f"nobody{n}", "password": "not the password"}
        )
    password_step(client)
    for _ in range(5):
        code_step(client, code=wrong_code(client))
    client.cookies.clear()
    add_user(client, "carla")

    assert password_step(client, "carla").status_code == 200


# --- finding 12: a wrong code undoes what its transaction did ----------------------------------


def test_with_code_rolls_back_when_the_code_was_wrong(client: TestClient, alice, clock) -> None:
    app = client.app
    with Session(app.state.engine) as db:
        user = users.get_by_username(db, "alice")
        db.expunge(user)
    request = Request({"type": "http", "app": app, "client": ("192.0.2.9", 1), "headers": []})

    def changes_then_fails(db: Session) -> None:
        users.get_user(db, user.id).theme = "grafite"
        db.flush()
        return None  # the code was wrong

    answer = reauth.with_code(request, user, clock.now, changes_then_fails)

    assert answer.status_code == 422
    with Session(app.state.engine) as db:
        assert users.get_user(db, user.id).theme == "moderno"  # undone
    assert "auth.two_factor_failed" in actions(client)

    def changes_and_succeeds(db: Session) -> str:
        users.get_user(db, user.id).theme = "ambra"
        return "done"

    assert reauth.with_code(request, user, clock.now, changes_and_succeeds) == "done"
    with Session(app.state.engine) as db:
        assert users.get_user(db, user.id).theme == "ambra"  # kept


def test_the_secret_is_never_logged_at_any_level(
    client: TestClient, pending, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.post(f"{TWO_FACTOR}/confirm", headers=CSRF, json={"code": wrong_code(client)})
        confirm(client, pending)

    logged = "\n".join(f"{record.getMessage()} {vars(record)}" for record in caplog.records)
    assert "code check: wrong code" in logged and pending not in logged
    assert totp.code_at(pending, totp.step_of(client.app.state.clock())) not in logged
