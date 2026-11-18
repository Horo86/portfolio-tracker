"""The security settings in the backup archive (D-180): the stored row is ordinary data of the
database, so it is in the archive and a restore on a new instance applies it from the first
start. An instance that never saved them restores to the environment's values. Invented data."""

from datetime import timedelta
from pathlib import Path

from test_upgrade_paths import CSRF, fixture_app, fixture_settings

from portfolio_tracker.backup import make_backup
from portfolio_tracker.config import Settings
from portfolio_tracker.restore import restore

ADMIN = {"username": "admin", "password": "fixture-admin-password"}
URL = "/api/v1/admin/security-settings"
SAVED = {
    "session_idle_days": 3,
    "session_absolute_days": 14,
    "login_account_failures": 4,
    "login_address_failures": 40,
    "block_first_minutes": 2,
    "block_max_minutes": 45,
    "require_2fa_all_users": False,
}


def test_the_saved_settings_survive_a_backup_and_a_restore(live: Settings, tmp_path: Path) -> None:
    with fixture_app(live) as client:
        assert client.post("/api/v1/auth/login", headers=CSRF, json=ADMIN).status_code == 200
        saved = client.put(URL, headers=CSRF, json={**SAVED, "password": ADMIN["password"]})
        assert saved.status_code == 200, saved.text
    archive = make_backup(live.db_path, live.manual_backup_dir).path
    target = fixture_settings(tmp_path / "new")

    restore(archive, target.data_dir)

    with fixture_app(target) as client:
        assert client.post("/api/v1/auth/login", headers=CSRF, json=ADMIN).status_code == 200
        body = client.get(URL).json()
        assert body["source"] == "database" and body["settings"] == SAVED
        assert client.app.state.session_policy.idle == timedelta(days=3)
        assert client.app.state.login_throttle.policy.max_block == timedelta(minutes=45)


def test_without_a_save_the_restored_instance_reads_the_environment(
    live: Settings, tmp_path: Path
) -> None:
    archive = make_backup(live.db_path, live.manual_backup_dir).path
    target = fixture_settings(tmp_path / "new")

    restore(archive, target.data_dir)

    with fixture_app(target) as client:
        assert client.post("/api/v1/auth/login", headers=CSRF, json=ADMIN).status_code == 200
        assert client.get(URL).json()["source"] == "environment"
