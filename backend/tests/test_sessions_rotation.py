"""D-164 (1) and (2): a password change gives the current session a new token, and a login
revokes the session carried by the incoming cookie."""

from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, add_user, bearer, create_admin, login_token
from sqlalchemy import select
from sqlalchemy.orm import Session

from portfolio_tracker.api.deps import SESSION_COOKIE
from portfolio_tracker.db.models import User, UserSession

NEW = "a brand new passphrase"


def session_users(client: TestClient) -> list[str]:
    with Session(client.app.state.engine) as db:
        rows = db.execute(select(User.username).join(UserSession, UserSession.user_id == User.id))
        return sorted(name for (name,) in rows)


def with_cookie(client: TestClient, value: str) -> int:
    """The status of `GET /me` with this cookie value and nothing else."""
    response = client.get("/api/v1/me", headers={"Cookie": f"{SESSION_COOKIE}={value}"})
    return response.status_code


def change_password(client: TestClient, headers: dict | None = None):
    return client.post(
        "/api/v1/me/password",
        headers=CSRF | (headers or {}),
        json={"current_password": PASSWORD, "new_password": NEW},
    )


# --- (1) a password change: a new token for the current session ----------------------------


def test_a_password_change_gives_the_cookie_session_a_new_token(client: TestClient) -> None:
    create_admin(client)
    old = client.cookies[SESSION_COOKIE]

    response = change_password(client)

    assert response.status_code == 200
    assert response.json()["token"] is None  # the web app gets the cookie only
    new = client.cookies[SESSION_COOKIE]
    assert new != old
    set_cookie = response.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag in set_cookie
    assert with_cookie(client, old) == 401  # the old token stops working at once
    assert with_cookie(client, new) == 200
    assert session_users(client) == ["admin"]  # the same session, not a second one


def test_a_password_change_gives_the_bearer_session_a_new_token(client: TestClient) -> None:
    create_admin(client)
    client.cookies.clear()
    old = login_token(client, "admin")
    other = login_token(client, "admin")

    response = change_password(client, bearer(old))

    assert response.status_code == 200
    new = response.json()["token"]
    assert new and new != old
    assert "set-cookie" not in response.headers
    assert client.get("/api/v1/me", headers=bearer(old)).status_code == 401
    assert client.get("/api/v1/me", headers=bearer(new)).status_code == 200
    assert client.get("/api/v1/me", headers=bearer(other)).status_code == 401  # D-93
    assert session_users(client) == ["admin"]


def test_a_password_change_keeps_the_session_s_absolute_expiry(client: TestClient, clock) -> None:
    create_admin(client)
    with Session(client.app.state.engine) as db:
        before = db.scalars(select(UserSession.expires_at)).one()
    clock.advance(seconds=3600)

    change_password(client)

    with Session(client.app.state.engine) as db:
        assert db.scalars(select(UserSession.expires_at)).one() == before
    max_age = int(client.app.state.session_policy.absolute.total_seconds()) - 3600
    assert f"Max-Age={max_age}" in change_password_cookie(client)


def change_password_cookie(client: TestClient) -> str:
    """The Set-Cookie of a second change, back to the first password (for its Max-Age)."""
    response = client.post(
        "/api/v1/me/password",
        headers=CSRF,
        json={"current_password": NEW, "new_password": PASSWORD + " again"},
    )
    assert response.status_code == 200, response.text
    return response.headers["set-cookie"]


def test_a_failed_password_change_keeps_the_token(client: TestClient) -> None:
    create_admin(client)
    old = client.cookies[SESSION_COOKIE]

    response = client.post(
        "/api/v1/me/password",
        headers=CSRF,
        json={"current_password": "not the password", "new_password": NEW},
    )

    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    assert with_cookie(client, old) == 200


def test_a_temporary_password_change_also_gives_a_new_token(client: TestClient) -> None:
    create_admin(client)
    add_user(client, "alice", must_change_password=True)
    client.post(
        "/api/v1/auth/login", headers=CSRF, json={"username": "alice", "password": PASSWORD}
    )
    old = client.cookies[SESSION_COOKIE]

    assert change_password(client).status_code == 200

    assert with_cookie(client, old) == 401
    assert client.get("/api/v1/accounts").status_code == 200


# --- (2) a login revokes the session in the incoming cookie --------------------------------


def login(client: TestClient, username: str, password: str = PASSWORD, **extra):
    return client.post(
        "/api/v1/auth/login",
        headers=CSRF,
        json={"username": username, "password": password} | extra,
    )


def test_a_login_as_another_user_revokes_the_previous_cookie_session(client: TestClient) -> None:
    create_admin(client)
    add_user(client, "alice")
    admins = client.cookies[SESSION_COOKIE]

    response = login(client, "alice")

    assert response.status_code == 200
    assert client.cookies[SESSION_COOKIE] != admins
    assert with_cookie(client, admins) == 401
    assert session_users(client) == ["alice"]  # no orphan session of the administrator


def test_a_login_as_the_same_user_replaces_the_cookie_session(client: TestClient) -> None:
    create_admin(client)
    old = client.cookies[SESSION_COOKIE]

    assert login(client, "admin").status_code == 200

    assert with_cookie(client, old) == 401  # no session fixation: the old value is dead
    assert session_users(client) == ["admin"]


def test_a_stale_cookie_does_not_stop_a_login(client: TestClient) -> None:
    create_admin(client)
    client.cookies.clear()

    response = client.post(
        "/api/v1/auth/login",
        headers=CSRF | {"Cookie": f"{SESSION_COOKIE}=not-a-session-token"},
        json={"username": "admin", "password": PASSWORD},
    )

    assert response.status_code == 200
    assert session_users(client) == ["admin", "admin"]  # the setup's session: not that cookie


def test_a_failed_login_keeps_the_cookie_session(client: TestClient) -> None:
    """Only a login that creates a session replaces the one in the cookie."""
    create_admin(client)
    old = client.cookies[SESSION_COOKIE]

    assert login(client, "admin", "not the password").status_code == 401

    assert with_cookie(client, old) == 200


def test_a_token_login_revokes_the_cookie_session_and_clears_the_cookie(
    client: TestClient,
) -> None:
    create_admin(client)
    add_user(client, "alice")
    admins = client.cookies[SESSION_COOKIE]

    response = login(client, "alice", issue_token=True)

    assert response.status_code == 200
    assert with_cookie(client, admins) == 401
    assert SESSION_COOKIE not in client.cookies  # cleared, not left pointing at nothing
    assert client.get("/api/v1/me", headers=bearer(response.json()["token"])).status_code == 200
    assert session_users(client) == ["alice"]


def test_a_bearer_header_does_not_revoke_anything_at_login(client: TestClient) -> None:
    """D-164 names the cookie only: a native client's token is its own to log out."""
    create_admin(client)
    client.cookies.clear()
    token = login_token(client, "admin")

    assert (
        client.post(
            "/api/v1/auth/login",
            headers=CSRF | bearer(token),
            json={"username": "admin", "password": PASSWORD, "issue_token": True},
        ).status_code
        == 200
    )

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 200
