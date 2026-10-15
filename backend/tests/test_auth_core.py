"""Auth core: Argon2id passwords, opaque sessions, and the FastAPI auth dependencies."""

import re
from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import (
    CSRF_HEADER,
    CSRF_VALUE,
    SESSION_COOKIE,
    AdminAuth,
    AuthAllowPasswordChange,
    CurrentAuth,
)
from portfolio_tracker.auth import sessions
from portfolio_tracker.auth.passwords import Passwords, policy_error, temporary_password
from portfolio_tracker.auth.sessions import SessionPolicy
from portfolio_tracker.db.models import User, UserSession
from portfolio_tracker.repositories import users

POLICY = SessionPolicy(idle=timedelta(days=7), absolute=timedelta(days=30))


# --- passwords --------------------------------------------------------------------------


def test_default_hasher_uses_argon2id_rfc9106_low_memory() -> None:
    encoded = Passwords().hash("correct horse battery")

    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_hash_and_verify(fast_passwords: Passwords) -> None:
    encoded = fast_passwords.hash("correct horse battery")

    assert encoded != "correct horse battery"
    assert fast_passwords.verify(encoded, "correct horse battery")
    assert not fast_passwords.verify(encoded, "correct horse batterY")
    assert not fast_passwords.verify("not-a-hash", "anything")
    fast_passwords.verify_dummy("whatever")  # never raises


def test_weaker_hashes_need_rehash(fast_passwords: Passwords) -> None:
    strong = Passwords(PasswordHasher(time_cost=2, memory_cost=16, parallelism=1))

    assert strong.needs_rehash(fast_passwords.hash("correct horse battery"))
    assert not strong.needs_rehash(strong.hash("correct horse battery"))


@pytest.mark.parametrize(
    ("password", "error"),
    [
        ("a" * 11, "PASSWORD_TOO_SHORT"),
        ("a" * 12, None),
        ("only lowercase letters", None),  # no composition rules (D-21)
        ("àèìòù€ßøåæœ!", None),  # 12 characters, not 12 bytes
        ("a" * 1025, "PASSWORD_TOO_LONG"),
    ],
)
def test_password_policy(password: str, error: str | None) -> None:
    assert policy_error(password) == error


def test_temporary_password_shape() -> None:
    generated = {temporary_password() for _ in range(50)}

    assert len(generated) == 50
    for password in generated:
        assert re.fullmatch(r"([A-HJ-NP-Za-km-z2-9]{4}-){3}[A-HJ-NP-Za-km-z2-9]{4}", password)
        assert policy_error(password) is None


# --- sessions ---------------------------------------------------------------------------


@pytest.fixture
def alice(db_session: Session) -> User:
    return users.add_user(
        db_session, username="alice", role="INVESTOR", password_hash="x", language="en"
    )


def test_only_the_token_hash_is_stored(db_session: Session, alice: User, clock) -> None:
    token, row = sessions.create_session(db_session, alice, clock.now, POLICY, "Firefox")

    assert len(token) >= 43  # 32 random bytes, url-safe base64
    assert row.token_hash == sessions.hash_token(token) != token
    assert row.expires_at == clock.now + timedelta(days=30)
    stored = db_session.scalars(select(UserSession.token_hash)).all()
    assert token not in stored


def test_session_resolves_to_its_user(db_session: Session, alice: User, clock) -> None:
    token, row = sessions.create_session(db_session, alice, clock.now, POLICY, None)

    assert sessions.resolve(db_session, token, clock.now, POLICY) == (row, alice)
    assert sessions.resolve(db_session, token + "x", clock.now, POLICY) is None


def test_idle_expiry(db_session: Session, alice: User, clock) -> None:
    token, _ = sessions.create_session(db_session, alice, clock.now, POLICY, None)

    clock.advance(days=6, hours=23)
    assert sessions.resolve(db_session, token, clock.now, POLICY) is not None  # touches it
    clock.advance(days=6, hours=23)
    assert sessions.resolve(db_session, token, clock.now, POLICY) is not None
    clock.advance(days=7)
    assert sessions.resolve(db_session, token, clock.now, POLICY) is None
    assert db_session.scalars(select(UserSession)).all() == []  # expired rows are removed


def test_absolute_expiry_even_when_used(db_session: Session, alice: User, clock) -> None:
    token, _ = sessions.create_session(db_session, alice, clock.now, POLICY, None)
    for _ in range(5):
        clock.advance(days=5, hours=23)  # used within the idle limit every time
        assert sessions.resolve(db_session, token, clock.now, POLICY) is not None
    clock.advance(hours=5)  # 30 days after login: expired exactly at the limit

    assert sessions.resolve(db_session, token, clock.now, POLICY) is None


def test_last_use_is_refreshed_at_most_every_few_minutes(
    db_session: Session, alice: User, clock
) -> None:
    token, row = sessions.create_session(db_session, alice, clock.now, POLICY, None)
    created = row.last_used_at

    clock.advance(minutes=1)
    sessions.resolve(db_session, token, clock.now, POLICY)
    assert row.last_used_at == created
    clock.advance(minutes=5)
    sessions.resolve(db_session, token, clock.now, POLICY)
    assert row.last_used_at == clock.now


def test_disabled_user_has_no_session(db_session: Session, alice: User, clock) -> None:
    token, _ = sessions.create_session(db_session, alice, clock.now, POLICY, None)
    alice.disabled_at = clock.now

    assert sessions.resolve(db_session, token, clock.now, POLICY) is None


def test_revoke_all_can_keep_the_current_session(db_session: Session, alice: User, clock) -> None:
    tokens = [sessions.create_session(db_session, alice, clock.now, POLICY, None) for _ in "abc"]
    keep = tokens[0][1]

    assert sessions.revoke_all(db_session, alice.id, except_id=keep.id) == 2
    assert db_session.scalars(select(UserSession)).all() == [keep]
    assert sessions.revoke_all(db_session, alice.id) == 1


# --- dependencies -----------------------------------------------------------------------


@pytest.fixture
def probe_app(app: FastAPI) -> FastAPI:
    """The real app plus probe routes that use each auth dependency."""

    @app.get("/probe/me")
    def me(auth: CurrentAuth) -> dict[str, str]:
        return {"username": auth.user.username}

    @app.post("/probe/write")
    def write(auth: CurrentAuth) -> dict[str, str]:
        return {"username": auth.user.username}

    @app.get("/probe/password")
    def password(auth: AuthAllowPasswordChange) -> dict[str, str]:
        return {"username": auth.user.username}

    @app.get("/probe/admin")
    def admin(auth: AdminAuth) -> dict[str, str]:
        return {"username": auth.user.username}

    return app


@pytest.fixture
def probe(probe_app: FastAPI):
    with TestClient(probe_app) as client:
        yield client


def login_as(client: TestClient, username: str, **fields) -> str:
    """Create a user and a session directly in the database; returns the token."""
    app = client.app
    with Session(app.state.engine, expire_on_commit=False) as db, db.begin():
        user = users.add_user(
            db, username=username, password_hash="x", language="en", **{"role": "INVESTOR"} | fields
        )
        token, _ = sessions.create_session(
            db, user, app.state.clock(), app.state.session_policy, None
        )
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_no_or_unknown_token_is_401(probe: TestClient) -> None:
    for headers in ({}, bearer("unknown")):
        response = probe.get("/probe/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHENTICATED"


def test_bearer_and_cookie_both_authenticate(probe: TestClient) -> None:
    token = login_as(probe, "alice")

    assert probe.get("/probe/me", headers=bearer(token)).json() == {"username": "alice"}
    probe.cookies.set(SESSION_COOKIE, token)
    assert probe.get("/probe/me").json() == {"username": "alice"}


def test_cookie_writes_need_the_csrf_header(probe: TestClient) -> None:
    token = login_as(probe, "alice")
    probe.cookies.set(SESSION_COOKIE, token)

    refused = probe.post("/probe/write")
    assert refused.status_code == 403 and refused.json()["code"] == "CSRF_CHECK_FAILED"
    wrong = probe.post("/probe/write", headers={CSRF_HEADER: "XMLHttpRequest"})
    assert wrong.status_code == 403
    assert probe.post("/probe/write", headers={CSRF_HEADER: CSRF_VALUE}).status_code == 200


def test_bearer_writes_need_no_csrf_header(probe: TestClient) -> None:
    token = login_as(probe, "alice")

    assert probe.post("/probe/write", headers=bearer(token)).status_code == 200


def test_temporary_password_blocks_everything_but_the_change(probe: TestClient) -> None:
    token = login_as(probe, "newbie", must_change_password=True)

    blocked = probe.get("/probe/me", headers=bearer(token))
    assert blocked.status_code == 403 and blocked.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
    assert probe.get("/probe/password", headers=bearer(token)).status_code == 200


def test_admin_routes_are_404_for_investors(probe: TestClient) -> None:
    investor = login_as(probe, "alice")
    admin = login_as(probe, "root", role="ADMINISTRATOR")

    hidden = probe.get("/probe/admin", headers=bearer(investor))
    assert hidden.status_code == 404 and hidden.json()["code"] == "NOT_FOUND"
    assert probe.get("/probe/admin", headers=bearer(admin)).status_code == 200


def test_expired_session_is_401(probe: TestClient, clock) -> None:
    token = login_as(probe, "alice")
    clock.advance(days=7, seconds=1)

    assert probe.get("/probe/me", headers=bearer(token)).status_code == 401
