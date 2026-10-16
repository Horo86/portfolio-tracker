"""Helpers for API tests. Invented users and passwords only."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import CSRF_HEADER, CSRF_VALUE
from portfolio_tracker.repositories import users

PASSWORD = "correct horse battery"
CSRF = {CSRF_HEADER: CSRF_VALUE}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_admin(client: TestClient, username: str = "admin", language: str = "en") -> dict:
    """Run the setup wizard; the client keeps the session cookie."""
    response = client.post(
        "/api/v1/setup/admin",
        json={"username": username, "password": PASSWORD, "language": language},
    )
    assert response.status_code == 201, response.text
    return response.json()


def login_token(client: TestClient, username: str, password: str = PASSWORD) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password, "issue_token": True},
    )
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
