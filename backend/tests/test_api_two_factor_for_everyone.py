"""Two-factor authentication for every user (D-183): an option of the security settings that
only adds an obligation. `PT_REQUIRE_2FA` keeps deciding for administrators; the option, when
on, obliges everyone, administrators too, also with `PT_REQUIRE_2FA=off`. Invented users only."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
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
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_api_admin_two_factor import two_step_login

from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import AuditEntry
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import security_settings as stored
from portfolio_tracker.repositories import two_factor, users
from portfolio_tracker.services.security_settings import refresh

URL = "/api/v1/admin/security-settings"
ME = "/api/v1/me"
ACCOUNTS = "/api/v1/accounts"
TWO_FACTOR = "/api/v1/me/two-factor"
VALUES = {"session_idle_days": 7, "session_absolute_days": 30, "login_account_failures": 5,
          "login_address_failures": 30, "block_first_minutes": 1, "block_max_minutes": 15,
          "require_2fa_all_users": False}  # fmt: skip


@contextmanager
def running(tmp_path: Path, require_2fa: str, fast_passwords, clock) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path / f"data-{require_2fa}", require_2fa=require_2fa)
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def oblige_everyone(client: TestClient, on: bool = True) -> None:
    """Store the option as a save would, without the step-up (tested on its own below)."""
    app = client.app
    with Session(app.state.engine) as db, db.begin():
        values = replace(app.state.security.values, require_2fa_all_users=on)
        stored.save(db, values, datetime(2026, 1, 15, tzinfo=UTC))
    refresh(app)


def obliged(client: TestClient, headers: dict) -> bool:
    return client.get(ME, headers=headers).json()["two_factor_setup_required"]


@pytest.mark.parametrize("require_2fa", ["admins", "off"])
@pytest.mark.parametrize("everyone", [False, True])
@pytest.mark.parametrize("role", ["INVESTOR", "ADMINISTRATOR"])
def test_who_must_enrol(tmp_path: Path, fast_passwords, clock, require_2fa, everyone, role):
    """Obliged when the option is on, or when an administrator under `PT_REQUIRE_2FA=admins`:
    the option never lifts the administrators' obligation, and `off` never lifts the option."""
    with running(tmp_path, require_2fa, fast_passwords, clock) as client:
        create_admin(client)
        add_user(client, "probe", role=role)
        headers = bearer(login_token(client, "probe"))
        if everyone:
            oblige_everyone(client)

        expected = everyone or (role == "ADMINISTRATOR" and require_2fa == "admins")
        assert obliged(client, headers) is expected
        status = client.get(ACCOUNTS, headers=headers).status_code
        assert status == (403 if expected else 200)


def test_the_option_is_saved_with_the_step_up_and_audited(client: TestClient) -> None:
    create_admin(client)

    response = client.put(
        URL, headers=CSRF, json={**VALUES, "require_2fa_all_users": True, "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert response.json()["settings"]["require_2fa_all_users"] is True
    assert client.app.state.security.values.require_2fa_all_users is True
    with Session(client.app.state.engine) as db:
        entry = db.scalars(
            select(AuditEntry).where(AuditEntry.action == "security.settings_changed")
        ).one()
    assert (entry.before["require_2fa_all_users"], entry.after["require_2fa_all_users"]) == (
        False,
        True,
    )
    # The administrator who saved it has no 2FA yet: stopped at the next request too.
    assert client.get(ACCOUNTS).json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_the_option_is_a_boolean_and_always_given(client: TestClient, value) -> None:
    create_admin(client)
    body = {**VALUES, "require_2fa_all_users": value, "password": PASSWORD}
    response = client.put(URL, headers=CSRF, json=body)
    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    missing = {**VALUES, "password": PASSWORD}
    del missing["require_2fa_all_users"]
    response = client.put(URL, headers=CSRF, json=missing)
    assert response.json()["code"] == "REQUEST_INVALID"
    assert client.app.state.security.values.require_2fa_all_users is False


def test_an_open_session_is_stopped_at_the_next_request_and_passes_after_enrolling(
    client: TestClient,
) -> None:
    create_admin(client)
    add_user(client, "bea")
    bea = bearer(login_token(client, "bea"))
    assert client.get(ACCOUNTS, headers=bea).status_code == 200

    saved = client.put(
        URL, headers=CSRF, json={**VALUES, "require_2fa_all_users": True, "password": PASSWORD}
    )
    assert saved.status_code == 200

    refused = client.get(ACCOUNTS, headers=bea)
    assert (refused.status_code, refused.json()["code"]) == (403, "TWO_FACTOR_SETUP_REQUIRED")
    assert obliged(client, bea) is True  # GET /me stays open: the page goes to the enrolment

    secret = client.post(f"{TWO_FACTOR}/setup", headers=bea, json={"password": PASSWORD}).json()[
        "secret"
    ]
    confirmed = client.post(
        f"{TWO_FACTOR}/confirm", headers=bea, json={"code": code_now(client, secret)}
    )
    assert confirmed.status_code == 200, confirmed.text
    bea = bearer(confirmed.json()["token"]) if confirmed.json().get("token") else bea

    assert client.get(ACCOUNTS, headers=bea).status_code == 200
    assert obliged(client, bea) is False


def test_after_an_administrator_removes_it_the_user_must_enrol_again(
    client: TestClient,
) -> None:
    create_admin(client)
    add_user(client, "bea")
    enable_two_factor(client, "bea")
    oblige_everyone(client)
    with Session(client.app.state.engine) as db:
        bea_id = users.get_by_username(db, "bea").id
    secret, _ = enable_two_factor(client, "admin")  # obliged too: the administrator enrols
    logged = two_step_login(client, "admin", secret)
    assert logged.status_code == 200, logged.text
    admin = bearer(logged.json()["token"])

    removed = client.post(
        f"/api/v1/admin/users/{bea_id}/disable-2fa",
        headers=admin,
        json={"password": PASSWORD, "code": code_now(client, secret, 1)},  # a code not used yet
    )
    assert removed.status_code == 200, removed.text

    bea = bearer(login_token(client, "bea"))  # the password alone, as D-174 (8)
    assert obliged(client, bea) is True
    assert client.get(ACCOUNTS, headers=bea).json()["code"] == "TWO_FACTOR_SETUP_REQUIRED"


def test_turning_the_option_off_obliges_nobody_new_and_removes_no_one(
    client: TestClient,
) -> None:
    create_admin(client)
    add_user(client, "bea")
    enable_two_factor(client, "bea")
    add_user(client, "carlo")
    carlo = bearer(login_token(client, "carlo"))
    oblige_everyone(client)
    assert obliged(client, carlo) is True

    oblige_everyone(client, on=False)

    assert obliged(client, carlo) is False
    assert client.get(ACCOUNTS, headers=carlo).status_code == 200
    with Session(client.app.state.engine) as db:
        assert two_factor.is_enabled(db, users.get_by_username(db, "bea").id)


def test_the_start_log_says_when_the_option_obliges_everyone_despite_off(
    tmp_path: Path, fast_passwords, clock, capsys
) -> None:
    import json

    with running(tmp_path, "off", fast_passwords, clock) as client:
        create_admin(client)
        oblige_everyone(client)
    capsys.readouterr()
    with running(tmp_path, "off", fast_passwords, clock):
        pass
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    warnings = [line["message"] for line in lines if line["level"] == "WARNING"]
    assert any("oblige every user" in message for message in warnings)
    assert not any("administrators are not obliged" in message for message in warnings)
