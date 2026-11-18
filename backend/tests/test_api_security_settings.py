"""The security settings API (D-180, D-181): administrators read and change the session
durations and the lockout thresholds; the values are stored once saved, apply without a
restart, and every save asks the password and, with two-factor authentication, a code.
Invented users and values only."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from portfolio_tracker.auth.security_settings import BOUNDS, DEFAULTS, SecuritySettings
from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import SECURITY_BOUNDS, AuditEntry, UserSession
from portfolio_tracker.main import create_app
from portfolio_tracker.repositories import security_settings as stored
from portfolio_tracker.repositories import users

URL = "/api/v1/admin/security-settings"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/me"
VALUES = {
    "session_idle_days": 7,
    "session_absolute_days": 30,
    "login_account_failures": 5,
    "login_address_failures": 30,
    "block_first_minutes": 1,
    "block_max_minutes": 15,
    "require_2fa_all_users": False,
}


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """The client holds the administrator's cookie session (the acting one)."""
    create_admin(client)
    return client


def save(client: TestClient, password: str = PASSWORD, **changes):
    return client.put(URL, headers=CSRF, json={**VALUES, **changes, "password": password})


def stored_row(client: TestClient):
    with Session(client.app.state.engine) as db:
        return stored.load(db)


def audit_entries(client: TestClient) -> list[AuditEntry]:
    with Session(client.app.state.engine) as db:
        query = select(AuditEntry).where(AuditEntry.action == "security.settings_changed")
        return list(db.scalars(query))


def pwchange_failures(client: TestClient) -> int:
    with Session(client.app.state.engine) as db:
        user = users.get_by_username(db, "admin")
    state = client.app.state.login_throttle._states.get(f"pwchange:{user.id}")
    return state.failures if state else 0


def memory(client: TestClient) -> tuple:
    state = client.app.state
    return state.security, state.session_policy, state.login_throttle.policy


def log_lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def failed_login(client: TestClient, username: str) -> int:
    saved, client.cookies = client.cookies, type(client.cookies)()
    try:
        response = client.post(
            LOGIN, headers=CSRF, json={"username": username, "password": "wrong password!"}
        )
    finally:
        client.cookies = saved
    return response.status_code


def test_the_model_repeats_the_bounds_of_the_settings() -> None:
    assert dict(BOUNDS) == SECURITY_BOUNDS


# --- reading -------------------------------------------------------------------------------


def test_a_new_instance_reads_the_environment_values(admin: TestClient) -> None:
    body = admin.get(URL).json()

    assert body["source"] == "environment" and body["updated_at"] is None
    assert body["settings"] == VALUES
    assert body["bounds"]["session_absolute_days"] == {"min": 1, "max": 90}
    assert body["bounds"]["login_address_failures"] == {"min": 10, "max": 200}
    assert stored_row(admin) is None  # nothing is stored before the first save


def test_the_environment_durations_are_the_initial_values(
    tmp_path: Path, fast_passwords, clock
) -> None:
    settings = Settings(data_dir=tmp_path / "env", session_idle_days=3, session_absolute_days=12)
    with TestClient(
        create_app(settings, passwords=fast_passwords, clock=clock), base_url="https://testserver"
    ) as client:
        create_admin(client)
        values = client.get(URL).json()["settings"]
    assert (values["session_idle_days"], values["session_absolute_days"]) == (3, 12)


# --- saving --------------------------------------------------------------------------------


@contextmanager
def running(settings: Settings, fast_passwords, clock) -> Iterator[TestClient]:
    """An app of its own, stopped at the end (its data folder is then free for another)."""
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_a_save_is_stored_and_read_by_a_new_app_on_the_same_folder(
    settings: Settings, fast_passwords, clock
) -> None:
    with running(settings, fast_passwords, clock) as first:
        create_admin(first)
        response = save(first, session_idle_days=8, block_max_minutes=60)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"] == "database" and body["updated_at"] is not None
        assert body["settings"]["session_idle_days"] == 8 and body["sessions_shortened"] == 0
        assert first.get(URL).json()["settings"] == {
            **VALUES,
            "session_idle_days": 8,
            "block_max_minutes": 60,
        }

    with running(settings, fast_passwords, clock) as client:
        token = login_token(client, "admin")
        read = client.get(URL, headers=bearer(token)).json()
        assert read["source"] == "database"
        assert read["settings"]["session_idle_days"] == 8
        assert client.app.state.session_policy.idle == timedelta(days=8)
        assert client.app.state.login_throttle.policy.max_block == timedelta(minutes=60)


def test_a_stored_value_wins_over_the_environment_and_the_start_says_so(
    settings: Settings, fast_passwords, clock, capsys
) -> None:
    with running(settings, fast_passwords, clock) as first:
        create_admin(first)
        assert save(first, session_idle_days=2).status_code == 200

    changed = settings.model_copy(update={"session_idle_days": 5})
    capsys.readouterr()
    with running(changed, fast_passwords, clock) as client:
        assert client.app.state.session_policy.idle == timedelta(days=2)
    said = [line for line in log_lines(capsys) if line.get("variable") == "PT_SESSION_IDLE_DAYS"]
    assert len(said) == 1 and "not used" in said[0]["message"]


def test_a_shorter_idle_time_applies_to_open_sessions_without_a_restart(
    admin: TestClient, clock
) -> None:
    add_user(admin, "bea")
    bea = bearer(login_token(admin, "bea"))
    clock.advance(days=2)
    assert admin.get(ME).status_code == 200  # the administrator's session is used meanwhile

    assert save(admin, session_idle_days=1).status_code == 200

    assert admin.get(ME, headers=bea).status_code == 401  # two days unused: ended
    assert admin.get(ME).status_code == 200


def test_a_lower_threshold_blocks_after_fewer_failures(admin: TestClient) -> None:
    add_user(admin, "carla")
    add_user(admin, "dario")
    for _ in range(5):
        assert failed_login(admin, "carla") == 401
    assert failed_login(admin, "carla") == 429  # five failures, then a block

    assert save(admin, login_account_failures=3).status_code == 200

    for _ in range(3):
        assert failed_login(admin, "dario") == 401
    assert failed_login(admin, "dario") == 429  # three failures now
    assert failed_login(admin, "carla") == 429  # a save never unblocks a key


def test_a_shorter_absolute_duration_caps_every_other_open_session(
    admin: TestClient, clock
) -> None:
    add_user(admin, "bea")
    bea = bearer(login_token(admin, "bea"))
    other = bearer(login_token(admin, "admin"))  # the administrator's other session
    for _ in range(2):  # used every few days: no session is idle
        clock.advance(days=3)
        for headers in (bea, other, None):
            assert admin.get(ME, headers=headers).status_code == 200
    clock.advance(hours=1)  # six days and an hour since every login

    response = save(admin, session_idle_days=5, session_absolute_days=5)

    assert response.status_code == 200, response.text
    assert response.json()["sessions_shortened"] == 2
    assert admin.get(ME, headers=bea).status_code == 401
    assert admin.get(ME, headers=other).status_code == 401
    assert admin.get(ME).status_code == 200  # the acting session keeps its end date


def test_a_shorter_absolute_duration_moves_the_end_of_a_session_still_open(
    admin: TestClient, clock
) -> None:
    add_user(admin, "bea")
    login_token(admin, "bea")
    start = clock()

    assert (
        save(admin, session_absolute_days=10, session_idle_days=7).json()["sessions_shortened"] == 1
    )

    with Session(admin.app.state.engine) as db:
        user = users.get_by_username(db, "bea")
        row = db.scalars(select(UserSession).where(UserSession.user_id == user.id)).one()
    assert row.expires_at == start + timedelta(days=10)


def test_a_longer_absolute_duration_applies_only_to_new_sessions(admin: TestClient, clock) -> None:
    add_user(admin, "bea")
    login_token(admin, "bea")

    assert save(admin, session_absolute_days=60).json()["sessions_shortened"] == 0

    with Session(admin.app.state.engine) as db:
        rows = list(db.scalars(select(UserSession)))
    assert {row.expires_at - row.created_at for row in rows} == {timedelta(days=30)}


def test_a_longer_idle_time_never_revives_a_session_rejected_earlier(
    admin: TestClient, clock
) -> None:
    add_user(admin, "bea")
    bea = bearer(login_token(admin, "bea"))
    clock.advance(days=8)  # past the 7 days: rejected, not yet cleaned up
    acting = bearer(login_token(admin, "admin"))

    response = admin.put(
        URL, headers=acting | CSRF, json={**VALUES, "session_idle_days": 20, "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert admin.get(ME, headers=bea).status_code == 401


def test_a_save_is_audited_with_before_and_after(admin: TestClient) -> None:
    response = save(admin, session_absolute_days=20)
    assert response.status_code == 200

    (entry,) = audit_entries(admin)
    assert entry.entity_type == "security_settings" and entry.actor_user_id is not None
    assert entry.before == {**VALUES, "source": "environment"}
    assert entry.after == {**VALUES, "session_absolute_days": 20, "sessions_shortened": 0}
    assert entry.ip

    save(admin, session_absolute_days=25)
    second = next(e for e in audit_entries(admin) if e.id != entry.id)
    assert second.before["source"] == "database" and second.before["session_absolute_days"] == 20


def test_a_save_of_the_stored_values_records_nothing(admin: TestClient) -> None:
    assert save(admin).status_code == 200  # the first save stores the values, recorded
    at = stored_row(admin)[1]

    again = save(admin)

    assert again.status_code == 200 and again.json()["sessions_shortened"] == 0
    assert len(audit_entries(admin)) == 1
    assert stored_row(admin)[1] == at


# --- refusals ------------------------------------------------------------------------------


def test_an_investor_gets_404(admin: TestClient) -> None:
    add_user(admin, "bea")
    bea = bearer(login_token(admin, "bea"))
    assert admin.get(URL, headers=bea).status_code == 404
    response = admin.put(URL, headers=bea | CSRF, json={**VALUES, "password": PASSWORD})
    assert (response.status_code, response.json()["code"]) == (404, "NOT_FOUND")


@pytest.mark.parametrize("value", ["7", 7.5, True, None])
def test_only_whole_numbers_are_accepted(admin: TestClient, value) -> None:
    response = save(admin, session_idle_days=value)
    assert (response.status_code, response.json()["code"]) == (422, "REQUEST_INVALID")
    assert stored_row(admin) is None


def test_a_missing_value_or_an_unknown_field_is_refused(admin: TestClient) -> None:
    body = {**VALUES, "password": PASSWORD}
    del body["block_max_minutes"]
    assert admin.put(URL, headers=CSRF, json=body).json()["code"] == "REQUEST_INVALID"
    unknown = {**VALUES, "password": PASSWORD, "session_days": 3}
    assert admin.put(URL, headers=CSRF, json=unknown).json()["code"] == "REQUEST_INVALID"


RANGE_CASES = [
    (name, value, low, high)
    for name, (low, high) in BOUNDS.items()
    for value in (low - 1, high + 1)
]


@pytest.mark.parametrize(("name", "value", "low", "high"), RANGE_CASES)
def test_each_bound_is_checked_before_the_password(
    admin: TestClient, name, value, low, high
) -> None:
    """A value out of its range is refused even with a wrong password: the form never counts
    as a guess (the per-user password count does not move) and never uses a code."""
    changes = {name: value}
    if name == "login_account_failures" and value > high:
        changes["login_address_failures"] = 200
    before = memory(admin)

    response = save(admin, password="not the password", **changes)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "SECURITY_SETTINGS_INVALID"
    assert {"field": name, "rule": "RANGE", "min": low, "max": high} in body["errors"]
    assert pwchange_failures(admin) == 0
    assert stored_row(admin) is None and memory(admin) == before


def test_idle_over_absolute_is_refused_with_the_absolute_as_maximum(admin: TestClient) -> None:
    response = save(admin, password="wrong", session_idle_days=10, session_absolute_days=8)

    assert response.json()["code"] == "SECURITY_SETTINGS_INVALID"
    assert response.json()["errors"] == [
        {"field": "session_idle_days", "rule": "IDLE_OVER_ABSOLUTE", "min": 1, "max": 8}
    ]
    assert pwchange_failures(admin) == 0


def test_an_address_threshold_under_twice_the_account_one_is_refused(admin: TestClient) -> None:
    response = save(admin, password="wrong", login_account_failures=8, login_address_failures=15)

    assert response.json()["errors"] == [
        {"field": "login_address_failures", "rule": "ADDRESS_UNDER_ACCOUNT", "min": 16, "max": 200}
    ]
    assert pwchange_failures(admin) == 0


def test_a_wrong_password_is_refused_and_counted(admin: TestClient) -> None:
    before = memory(admin)

    response = save(admin, password="not the password", session_idle_days=3)

    assert (response.status_code, response.json()["code"]) == (422, "CURRENT_PASSWORD_WRONG")
    assert pwchange_failures(admin) == 1
    assert stored_row(admin) is None and memory(admin) == before
    assert audit_entries(admin) == []


def test_wrong_passwords_lead_to_429(admin: TestClient) -> None:
    for _ in range(5):
        assert save(admin, password="not the password").status_code == 422
    response = save(admin)
    assert (response.status_code, response.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert stored_row(admin) is None


@pytest.fixture
def admin_2fa(admin: TestClient) -> Iterator[tuple[TestClient, str, list[str]]]:
    secret, codes = enable_two_factor(admin, "admin")
    yield admin, secret, codes


def test_with_two_factor_a_code_is_required(admin_2fa) -> None:
    client, _, _ = admin_2fa
    before = memory(client)

    response = save(client, session_idle_days=3)

    assert (response.status_code, response.json()["code"]) == (422, "TWO_FACTOR_CODE_REQUIRED")
    assert stored_row(client) is None and memory(client) == before


def test_with_two_factor_a_wrong_code_is_refused(admin_2fa) -> None:
    client, _, _ = admin_2fa
    before = memory(client)

    response = client.put(
        URL,
        headers=CSRF,
        json={**VALUES, "session_idle_days": 3, "password": PASSWORD, "code": wrong_code(client)},
    )

    assert (response.status_code, response.json()["code"]) == (422, "TWO_FACTOR_CODE_WRONG")
    assert stored_row(client) is None and memory(client) == before
    assert audit_entries(client) == []


def test_with_two_factor_a_code_or_a_recovery_code_saves(admin_2fa) -> None:
    client, secret, codes = admin_2fa
    body = {**VALUES, "password": PASSWORD}

    first = client.put(
        URL, headers=CSRF, json={**body, "session_idle_days": 3, "code": code_now(client, secret)}
    )
    second = client.put(
        URL, headers=CSRF, json={**body, "session_idle_days": 4, "recovery_code": codes[0]}
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert client.app.state.session_policy.idle == timedelta(days=4)


def test_wrong_codes_lead_to_429(admin_2fa) -> None:
    client, secret, _ = admin_2fa
    body = {**VALUES, "password": PASSWORD}
    for _ in range(5):
        response = client.put(URL, headers=CSRF, json={**body, "code": wrong_code(client)})
        assert response.json()["code"] == "TWO_FACTOR_CODE_WRONG"
    response = client.put(URL, headers=CSRF, json={**body, "code": code_now(client, secret)})
    assert (response.status_code, response.json()["code"]) == (429, "TOO_MANY_ATTEMPTS")
    assert stored_row(client) is None


# --- a stored row outside the bounds ---------------------------------------------------------


def _store_by_hand(settings: Settings, fast_passwords, clock, **values) -> None:
    """A row as a hand edit could leave it: the CHECK constraints are switched off."""
    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver") as client:
        create_admin(client)
        with app.state.engine.begin() as connection:
            connection.execute(text("PRAGMA ignore_check_constraints = ON"))
            columns = {**VALUES, **values}
            names = ", ".join(columns)
            connection.execute(
                text(
                    f"INSERT INTO security_settings (id, {names},"
                    f" updated_at) VALUES (1, {', '.join(f':{n}' for n in columns)},"
                    " '2026-01-01 00:00:00.000000')"
                ),
                columns,
            )
            connection.execute(text("PRAGMA ignore_check_constraints = OFF"))


def test_a_stored_row_outside_the_bounds_is_adjusted_at_start(
    settings: Settings, fast_passwords, clock, capsys
) -> None:
    _store_by_hand(
        settings,
        fast_passwords,
        clock,
        session_absolute_days=400,
        block_max_minutes=5,
        login_address_failures=6,
    )

    capsys.readouterr()
    with running(settings, fast_passwords, clock) as client:
        token = login_token(client, "admin")
        body = client.get(URL, headers=bearer(token)).json()

    assert body["source"] == "database"
    assert body["settings"] == {
        **VALUES,
        "session_absolute_days": 90,
        "block_max_minutes": 15,
        "login_address_failures": 10,
    }
    adjusted = {line.get("setting") for line in log_lines(capsys) if line["level"] == "WARNING"}
    assert {"session_absolute_days", "block_max_minutes", "login_address_failures"} <= adjusted


def test_a_stored_row_breaking_a_cross_rule_is_adjusted_at_start(
    settings: Settings, fast_passwords, clock
) -> None:
    _store_by_hand(
        settings,
        fast_passwords,
        clock,
        session_idle_days=20,
        session_absolute_days=10,
        login_account_failures=12,
        login_address_failures=15,
    )

    app = create_app(settings, passwords=fast_passwords, clock=clock)
    with TestClient(app, base_url="https://testserver"):
        values = app.state.security.values

    assert values == SecuritySettings(
        **{
            **VALUES,
            "session_idle_days": 10,
            "session_absolute_days": 10,
            "login_account_failures": 12,
            "login_address_failures": 24,
        }
    )
    assert DEFAULTS.session_idle_days == 7  # the defaults are not touched
