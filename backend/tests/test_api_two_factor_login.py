"""Login in two steps (D-174): password, then a TOTP code or a recovery code.

Two-factor authentication is switched on directly in the database (`enable_two_factor`): the
routes that enable it are tested on their own. Invented users, secrets and codes only.
"""

import logging
import threading

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
    wrong_code,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import SESSION_COOKIE
from portfolio_tracker.api.routes.auth import STEP_COOKIE, STEP_PATH
from portfolio_tracker.db.models import AuditEntry, RecoveryCode, User, UserSession
from portfolio_tracker.repositories import two_factor, users

LOGIN = "/api/v1/auth/login"
SECOND = "/api/v1/auth/login/two-factor"


@pytest.fixture
def alice(client: TestClient) -> tuple[str, list[str]]:
    """An administrator exists (logged out again) and alice has two-factor authentication."""
    create_admin(client)
    client.post("/api/v1/auth/logout", headers=CSRF)
    client.cookies.clear()
    add_user(client, "alice")
    return enable_two_factor(client, "alice")


def first_step(client: TestClient, username: str = "alice", **extra):
    body = {"username": username, "password": PASSWORD, **extra}
    return client.post(LOGIN, headers=CSRF, json=body)


def second_step(client: TestClient, **body):
    return client.post(SECOND, headers=CSRF, json=body)


def sessions_of(client: TestClient, username: str = "alice") -> int:
    with Session(client.app.state.engine) as db:
        user = users.get_by_username(db, username)
        return len(db.scalars(select(UserSession).where(UserSession.user_id == user.id)).all())


def audit(client: TestClient) -> list[tuple[str, dict | None]]:
    with Session(client.app.state.engine) as db:
        rows = db.scalars(select(AuditEntry).order_by(AuditEntry.at, AuditEntry.id))
        return [(row.action, row.after) for row in rows]


# --- the first step ---------------------------------------------------------------------


def test_a_right_password_opens_no_session_when_the_user_has_two_factor(
    client: TestClient, alice
) -> None:
    response = first_step(client)

    assert response.status_code == 200
    assert response.json() == {"two_factor_required": True, "challenge": None, "expires_in": 300}
    assert SESSION_COOKIE not in client.cookies and sessions_of(client) == 0
    with Session(client.app.state.engine) as db:
        assert users.get_by_username(db, "alice").last_login_at is None


def test_the_proof_is_an_http_only_cookie_sent_only_to_the_second_step(
    client: TestClient, alice
) -> None:
    header = first_step(client).headers["set-cookie"]

    assert header.startswith(f"{STEP_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Max-Age=300", f"Path={STEP_PATH}"):
        assert flag in header, flag


def test_the_proof_is_not_a_session(client: TestClient, alice) -> None:
    proof = first_step(client, issue_token=True).json()["challenge"]
    client.cookies.clear()

    assert client.get("/api/v1/me", headers=bearer(proof)).status_code == 401
    client.cookies.set(SESSION_COOKIE, proof)
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/accounts").status_code == 401
    assert sessions_of(client) == 0


def test_a_wrong_password_looks_the_same_with_and_without_two_factor(
    client: TestClient, alice
) -> None:
    add_user(client, "bob")
    answers = [
        client.post(LOGIN, headers=CSRF, json={"username": name, "password": "not the password"})
        for name in ("alice", "bob", "nobody")
    ]

    assert {(a.status_code, a.text) for a in answers} == {(401, answers[0].text)}
    assert all("set-cookie" not in a.headers for a in answers)


def test_a_user_without_two_factor_logs_in_in_one_step(client: TestClient, alice) -> None:
    add_user(client, "bob")

    response = first_step(client, "bob")

    assert response.status_code == 200 and response.json()["user"]["username"] == "bob"
    assert "two_factor_required" not in response.json()
    assert client.get("/api/v1/me").status_code == 200


def test_a_pending_enrolment_is_not_two_factor(client: TestClient, alice) -> None:
    add_user(client, "bob")
    with Session(client.app.state.engine) as db, db.begin():
        bob = users.get_by_username(db, "bob")
        two_factor.begin(db, bob.id, "A" * 32, client.app.state.clock())

    assert first_step(client, "bob").json()["user"]["username"] == "bob"


# --- the second step --------------------------------------------------------------------


def test_the_code_opens_the_session(client: TestClient, alice, clock) -> None:
    secret, _ = alice
    first_step(client)

    response = second_step(client, code=code_now(client, secret))

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "alice" and body["token"] is None
    assert client.get("/api/v1/me").json()["username"] == "alice"
    assert sessions_of(client) == 1
    with Session(client.app.state.engine) as db:
        assert users.get_by_username(db, "alice").last_login_at == clock.now
    assert ("auth.login", {"second_factor": "totp"}) in audit(client)


def test_a_success_consumes_the_proof_and_removes_its_cookie(client: TestClient, alice) -> None:
    secret, _ = alice
    first_step(client)
    proof = client.cookies.get(STEP_COOKIE, path=STEP_PATH)

    done = second_step(client, code=code_now(client, secret))

    cleared = [h for h in done.headers.get_list("set-cookie") if h.startswith(f"{STEP_COOKIE}=")]
    assert len(cleared) == 1 and "Max-Age=0" in cleared[0]
    assert len(client.app.state.login_challenges) == 0
    again = second_step(client, code=code_now(client, secret, 1), challenge=proof)
    assert (again.status_code, again.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_a_code_one_step_either_side_is_accepted(client: TestClient, alice, offset: int) -> None:
    secret, _ = alice
    first_step(client)

    assert second_step(client, code=code_now(client, secret, offset)).status_code == 200


@pytest.mark.parametrize("offset", [-2, 2])
def test_a_code_two_steps_away_is_wrong(client: TestClient, alice, offset: int) -> None:
    secret, _ = alice
    first_step(client)

    response = second_step(client, code=code_now(client, secret, offset))

    assert (response.status_code, response.json()["code"]) == (401, "TWO_FACTOR_CODE_WRONG")


def test_a_bearer_client_carries_the_proof_in_the_body(client: TestClient, alice) -> None:
    secret, _ = alice
    step = first_step(client, issue_token=True)
    assert "set-cookie" not in step.headers
    proof = step.json()["challenge"]
    client.cookies.clear()

    response = second_step(client, code=code_now(client, secret), challenge=proof)

    token = response.json()["token"]
    assert token and "set-cookie" not in response.headers
    assert client.get("/api/v1/me", headers=bearer(token)).json()["username"] == "alice"


def test_the_second_step_needs_the_csrf_header(client: TestClient, alice) -> None:
    secret, _ = alice
    first_step(client)

    response = client.post(SECOND, json={"code": code_now(client, secret)})

    assert (response.status_code, response.json()["code"]) == (403, "CSRF_CHECK_FAILED")
    assert sessions_of(client) == 0


def test_without_a_proof_there_is_no_second_step(client: TestClient, alice) -> None:
    secret, _ = alice

    for body in (
        {"code": code_now(client, secret)},
        {"code": wrong_code(client), "challenge": "made-up"},
    ):
        response = second_step(client, **body)
        assert (response.status_code, response.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")
    assert sessions_of(client) == 0


@pytest.mark.parametrize(
    "body", [{}, {"code": "123456", "recovery_code": "abcd-efgh-jkmn-pqrs"}, {"code": "1" * 33},
             {"code": "123456", "extra": 1}],
)  # fmt: skip
def test_the_body_is_one_code_or_one_recovery_code(client: TestClient, alice, body: dict) -> None:
    first_step(client)

    response = second_step(client, **body)

    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert "123456" not in response.text and "abcd" not in response.text


def test_a_wrong_code_does_not_consume_the_proof(client: TestClient, alice) -> None:
    secret, _ = alice
    first_step(client)

    wrong = second_step(client, code=wrong_code(client))

    assert (wrong.status_code, wrong.json()["code"]) == (401, "TWO_FACTOR_CODE_WRONG")
    assert wrong.json()["attempts_left"] == 4 and "set-cookie" not in wrong.headers
    assert second_step(client, code=code_now(client, secret)).status_code == 200
    assert ("auth.two_factor_failed", None) in audit(client)


def test_the_proof_lasts_five_minutes(client: TestClient, alice, clock) -> None:
    secret, _ = alice
    first_step(client)
    clock.advance(minutes=5)

    response = second_step(client, code=code_now(client, secret))

    assert (response.status_code, response.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert sessions_of(client) == 0


def test_a_code_works_once(client: TestClient, alice) -> None:
    secret, _ = alice
    code = code_now(client, secret)
    first_step(client)
    assert second_step(client, code=code).status_code == 200
    client.cookies.clear()

    first_step(client)
    reused = second_step(client, code=code)

    assert (reused.status_code, reused.json()["code"]) == (401, "TWO_FACTOR_CODE_WRONG")
    older = second_step(client, code=code_now(client, secret, -1))
    assert older.status_code == 401  # a step at or below the last used one
    assert second_step(client, code=code_now(client, secret, 1)).status_code == 200


def test_the_same_code_in_two_requests_at_once_opens_one_session(client: TestClient, alice) -> None:
    secret, _ = alice
    code = code_now(client, secret)
    proofs = [first_step(client, issue_token=True).json()["challenge"] for _ in range(6)]
    client.cookies.clear()
    start = threading.Barrier(len(proofs))
    statuses: list[int] = []

    def request(proof: str) -> None:
        start.wait()
        response = client.post(SECOND, headers=CSRF, json={"code": code, "challenge": proof})
        statuses.append(response.status_code)

    threads = [threading.Thread(target=request, args=(proof,)) for proof in proofs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses.count(200) == 1 and sessions_of(client) == 1
    assert set(statuses) <= {200, 401, 429}


# --- attempt limits -----------------------------------------------------------------------


def test_five_wrong_codes_use_up_the_proof_and_block_the_user(
    client: TestClient, alice, clock, caplog: pytest.LogCaptureFixture
) -> None:
    secret, _ = alice
    first_step(client)

    left = [second_step(client, code=wrong_code(client)).json()["attempts_left"] for _ in range(5)]

    assert left == [4, 3, 2, 1, 0]
    assert len(client.app.state.login_challenges) == 0  # the proof is gone
    gone = second_step(client, code=code_now(client, secret))
    assert (gone.status_code, gone.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")

    # A new password step is allowed (the password is right), its code is not, for a minute.
    with caplog.at_level(logging.WARNING, logger="portfolio_tracker.api.routes.auth"):
        assert first_step(client).status_code == 200
        blocked = second_step(client, code=code_now(client, secret))
        second_step(client, code=code_now(client, secret))
    assert (blocked.status_code, blocked.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert blocked.json()["retry_after"] == 60 and blocked.headers["Retry-After"] == "60"
    assert [r.message for r in caplog.records].count("login code blocked") == 1  # once per block
    assert sessions_of(client) == 0

    clock.advance(seconds=60)
    assert second_step(client, code=code_now(client, secret)).status_code == 200  # same proof


def test_the_block_doubles_with_each_further_wrong_code(client: TestClient, alice, clock) -> None:
    for _ in range(5):
        first_step(client)
        second_step(client, code=wrong_code(client))
    waits = []
    for _ in range(5):
        clock.advance(seconds=waits[-1] if waits else 60)
        first_step(client)
        assert second_step(client, code=wrong_code(client)).status_code == 401
        waits.append(second_step(client, code=wrong_code(client)).json()["retry_after"])

    assert waits == [120, 240, 480, 900, 900]  # doubling, capped at 15 minutes


def test_a_blocked_code_does_not_use_the_proof_s_attempts(client: TestClient, alice, clock) -> None:
    secret, _ = alice
    first_step(client)
    for _ in range(5):
        second_step(client, code=wrong_code(client))
    first_step(client)
    for _ in range(10):
        assert second_step(client, code=wrong_code(client)).status_code == 429

    clock.advance(seconds=60)

    assert second_step(client, code=code_now(client, secret)).status_code == 200


def test_a_right_code_clears_the_count(client: TestClient, alice) -> None:
    secret, _ = alice
    for offset in (0, 1):
        first_step(client)
        for _ in range(4):
            assert second_step(client, code=wrong_code(client)).status_code == 401
        assert second_step(client, code=code_now(client, secret, offset)).status_code == 200
        client.cookies.clear()


def test_wrong_codes_do_not_block_the_password_step_or_another_user(
    client: TestClient, alice
) -> None:
    add_user(client, "bob")
    bob_secret, _ = enable_two_factor(client, "bob")
    first_step(client)
    for _ in range(5):
        second_step(client, code=wrong_code(client))

    assert first_step(client).status_code == 200  # alice's password step
    assert first_step(client, "bob").status_code == 200
    assert second_step(client, code=code_now(client, bob_secret)).status_code == 200


def test_wrong_passwords_and_wrong_codes_are_counted_apart(client: TestClient, alice) -> None:
    secret, _ = alice
    for _ in range(4):
        client.post(LOGIN, headers=CSRF, json={"username": "alice", "password": "wrong password"})
    first_step(client)  # a right password clears the password count
    for _ in range(4):
        second_step(client, code=wrong_code(client))

    assert second_step(client, code=code_now(client, secret)).status_code == 200


# --- recovery codes ---------------------------------------------------------------------


def test_a_recovery_code_opens_the_session_once(client: TestClient, alice) -> None:
    _, codes = alice
    first_step(client)

    response = second_step(client, recovery_code=codes[2])

    assert response.status_code == 200 and response.json()["user"]["username"] == "alice"
    assert ("auth.login", {"second_factor": "recovery_code", "sessions_revoked": 0}) in audit(
        client
    )
    with Session(client.app.state.engine) as db:
        assert (
            len(db.scalars(select(RecoveryCode).where(RecoveryCode.used_at.is_(None))).all()) == 9
        )
    client.cookies.clear()

    first_step(client)
    again = second_step(client, recovery_code=codes[2])
    assert (again.status_code, again.json()["code"]) == (401, "TWO_FACTOR_CODE_WRONG")
    assert second_step(client, recovery_code=codes[3].replace("-", " ")).status_code == 200


def test_a_recovery_code_ends_the_user_s_other_sessions(client: TestClient, alice) -> None:
    secret, codes = alice
    first_step(client)
    old = second_step(client, code=code_now(client, secret)).json()
    old_cookie = client.cookies.get(SESSION_COOKIE)
    assert old["user"]["username"] == "alice" and sessions_of(client) == 1
    client.cookies.clear()

    first_step(client)
    assert second_step(client, recovery_code=codes[0]).status_code == 200

    assert sessions_of(client) == 1  # only the new one
    new_cookie = client.cookies.get(SESSION_COOKIE)
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, old_cookie)
    assert client.get("/api/v1/me").status_code == 401
    client.cookies.set(SESSION_COOKIE, new_cookie)
    assert client.get("/api/v1/me").status_code == 200


def test_a_totp_code_is_not_a_recovery_code_and_the_reverse(client: TestClient, alice) -> None:
    secret, codes = alice
    first_step(client)

    assert second_step(client, recovery_code=code_now(client, secret)).status_code == 401
    assert second_step(client, code=codes[0]).status_code == 401
    assert sessions_of(client) == 0


def test_another_user_s_codes_are_wrong(client: TestClient, alice) -> None:
    add_user(client, "bob")
    bob_secret, bob_codes = enable_two_factor(client, "bob")
    first_step(client)

    assert second_step(client, code=code_now(client, bob_secret)).status_code == 401
    assert second_step(client, recovery_code=bob_codes[0]).status_code == 401
    assert sessions_of(client) == 0 and sessions_of(client, "bob") == 0


# --- what changes between the two steps ---------------------------------------------------


def _change(client: TestClient, **values) -> None:
    with Session(client.app.state.engine) as db, db.begin():
        user = users.get_by_username(db, "alice")
        for name, value in values.items():
            setattr(user, name, value)


@pytest.mark.parametrize("change", ["disabled", "password"])
def test_a_user_disabled_or_a_password_changed_meanwhile_ends_the_pending_login(
    client: TestClient, alice, change: str
) -> None:
    secret, _ = alice
    first_step(client)
    if change == "disabled":
        _change(client, disabled_at=client.app.state.clock())
    else:
        _change(client, password_hash=client.app.state.passwords.hash("another password!"))

    response = second_step(client, code=code_now(client, secret))

    assert (response.status_code, response.json()["code"]) == (401, "LOGIN_STEP_EXPIRED")
    assert sessions_of(client) == 0 and len(client.app.state.login_challenges) == 0
    with Session(client.app.state.engine) as db:
        assert two_factor.get(db, users.get_by_username(db, "alice").id).last_step is None


def test_a_login_ends_the_session_in_the_incoming_cookie(client: TestClient, alice) -> None:
    """D-164 still holds when the session opens at the second step."""
    secret, _ = alice
    add_user(client, "bob")
    first_step(client, "bob")
    assert sessions_of(client, "bob") == 1

    first_step(client)
    assert sessions_of(client, "bob") == 1  # nothing ended by the password alone
    second_step(client, code=code_now(client, secret))

    assert sessions_of(client, "bob") == 0 and sessions_of(client) == 1


def test_a_temporary_password_is_still_changed_first(client: TestClient, alice) -> None:
    secret, _ = alice
    _change(client, must_change_password=True)
    first_step(client)

    response = second_step(client, code=code_now(client, secret))

    assert response.json()["user"]["must_change_password"] is True
    assert client.get("/api/v1/accounts").json()["code"] == "PASSWORD_CHANGE_REQUIRED"


# --- nothing leaks ------------------------------------------------------------------------


def test_no_secret_code_or_proof_reaches_the_logs_the_errors_or_the_audit_log(
    client: TestClient, alice, caplog: pytest.LogCaptureFixture
) -> None:
    secret, codes = alice
    typed = [wrong_code(client), "zzzz-zzzz-zzzz-zzzz"]
    answers = []
    with caplog.at_level(logging.DEBUG):
        proof = first_step(client, issue_token=True).json()["challenge"]
        answers.append(second_step(client, code=typed[0], challenge=proof))
        answers.append(second_step(client, code=typed[0], challenge=proof))
        answers.append(second_step(client, recovery_code=typed[1], challenge=proof))
        right = code_now(client, secret)
        answers.append(second_step(client, code=right, challenge=proof))
        first_step(client)
        answers.append(second_step(client, recovery_code=codes[0]))

    assert [a.status_code for a in answers] == [401, 401, 401, 200, 200]
    logged = "\n".join(f"{record.getMessage()} {vars(record)}" for record in caplog.records)
    stored = repr(audit(client))
    errors = "".join(a.text for a in answers[:3])
    for value in (secret, proof, right, codes[0], codes[0].replace("-", ""), *typed):
        assert value not in logged, value
        assert value not in stored, value
        assert value not in errors, value
    with Session(client.app.state.engine) as db:
        alice_id = db.scalars(select(User.id).where(User.username == "alice")).one()
    assert f"'user_id': '{alice_id}'" in logged  # who, never what was typed
