"""Helpers for API tests. Invented users and passwords only."""

import hashlib

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import CSRF_HEADER, CSRF_VALUE
from portfolio_tracker.auth import recovery, totp
from portfolio_tracker.identity import is_valid_email, username_key
from portfolio_tracker.repositories import two_factor, users

PASSWORD = "correct horse battery"
CSRF = {CSRF_HEADER: CSRF_VALUE}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def details(username: str) -> dict[str, str]:
    """Invented registration details for `username` (D-173): its e-mail is derived from it, so
    two users of a test never share one."""
    key = username_key(username)
    email = f"{key}@example.org"
    if not is_valid_email(email):
        email = f"user-{hashlib.sha256(key.encode()).hexdigest()[:12]}@example.org"
    return {"first_name": "Example", "last_name": "User", "email": email}


def create_admin(client: TestClient, username: str = "admin", language: str = "en") -> dict:
    """Run the setup wizard; the client keeps the session cookie."""
    response = client.post(
        "/api/v1/setup/admin",
        headers=CSRF,
        json={"username": username, "password": PASSWORD, "language": language} | details(username),
    )
    assert response.status_code == 201, response.text
    return response.json()


def login_token(client: TestClient, username: str, password: str = PASSWORD) -> str:
    """A native client's login: it carries no browser cookie, so the client's own cookie
    session, which a login would end (D-164), is left alone."""
    saved, client.cookies = client.cookies, type(client.cookies)()
    try:
        response = client.post(
            "/api/v1/auth/login",
            headers=CSRF,
            json={"username": username, "password": password, "issue_token": True},
        )
    finally:
        client.cookies = saved
    assert response.status_code == 200, response.text
    return response.json()["token"]


def add_user(
    client: TestClient,
    username: str,
    *,
    role: str = "INVESTOR",
    password: str = PASSWORD,
    must_change_password: bool = False,
    disabled: bool = False,
) -> None:
    """Insert a user directly (the admin user API arrives in step 6)."""
    app = client.app
    with Session(app.state.engine) as db, db.begin():
        users.add_user(
            db,
            username=username,
            **details(username),
            role=role,
            password_hash=app.state.passwords.hash(password),
            language="en",
            must_change_password=must_change_password,
            disabled_at=app.state.clock() if disabled else None,
        )


def investor(client: TestClient, username: str) -> dict[str, str]:
    """A new investor and their bearer headers."""
    add_user(client, username)
    return bearer(login_token(client, username))


def fake_isin(serial: int) -> str:
    """A syntactically valid ISIN that belongs to no real security ("ZZ" is not a country)."""
    body = f"ZZ{serial:09d}"
    digits = "".join(str(int(ch, 36)) for ch in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n // 10 + n % 10
    return body + str((10 - total % 10) % 10)


def enable_two_factor(client: TestClient, username: str) -> tuple[str, list[str]]:
    """Give `username` confirmed two-factor authentication directly in the database.
    Returns the invented secret and the ten recovery codes."""
    app = client.app
    secret, codes = totp.new_secret(), recovery.new_codes()
    with Session(app.state.engine) as db, db.begin():
        user = users.get_by_username(db, username)
        assert user is not None, username
        two_factor.begin(db, user.id, secret, app.state.clock())
        two_factor.confirm(db, user.id, app.state.clock())
        two_factor.replace_recovery_codes(
            db, user.id, map(recovery.hash_code, codes), app.state.clock()
        )
    return secret, codes


def code_now(client: TestClient, secret: str, offset: int = 0) -> str:
    """The TOTP code of the app's current step (or `offset` steps away)."""
    return totp.code_at(secret, totp.step_of(client.app.state.clock()) + offset)
