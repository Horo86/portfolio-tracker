"""Two-factor authentication in the backup archive (D-174): the secret is stored plain and the
recovery codes as hashes, both in the database, so both are in the archive and a restore on a
new instance keeps the two-step login working, without the `secrets` folder. Invented data."""

import zipfile
from pathlib import Path

from sqlalchemy.orm import Session
from test_upgrade_paths import CSRF, FIXED_NOW, INVESTOR, fixture_app, fixture_settings

from portfolio_tracker.auth import recovery, totp
from portfolio_tracker.backup import make_backup
from portfolio_tracker.config import Settings
from portfolio_tracker.db import create_db_engine
from portfolio_tracker.repositories import two_factor, users
from portfolio_tracker.restore import restore

LOGIN = "/api/v1/auth/login"


def enrolled(settings: Settings) -> tuple[str, list[str]]:
    """Give the fixture's investor two-factor authentication, in the database."""
    secret, codes = totp.new_secret(), recovery.new_codes()
    engine = create_db_engine(settings.db_path)
    try:
        with Session(engine) as db, db.begin():
            user = users.get_by_username(db, INVESTOR[0])
            two_factor.begin(db, user.id, secret, FIXED_NOW)
            two_factor.confirm(db, user.id, FIXED_NOW)
            two_factor.replace_recovery_codes(
                db, user.id, map(recovery.hash_code, codes), FIXED_NOW
            )
    finally:
        engine.dispose()
    return secret, codes


def test_two_factor_survives_a_backup_and_a_restore_on_a_new_instance(
    live: Settings, tmp_path: Path
) -> None:
    secret, codes = enrolled(live)
    archive = make_backup(live.db_path, live.manual_backup_dir).path
    target = fixture_settings(tmp_path / "new")

    restore(archive, target.data_dir)

    assert not (target.data_dir / "secrets").exists()  # nothing of the old instance's folder
    password = {"username": INVESTOR[0], "password": INVESTOR[1]}
    with fixture_app(target) as client:
        step = client.post(LOGIN, headers=CSRF, json=password)
        assert step.status_code == 200 and step.json()["two_factor_required"] is True
        assert client.get("/api/v1/me").status_code == 401  # the password alone opens nothing

        code = totp.code_at(secret, totp.step_of(FIXED_NOW))
        logged = client.post(f"{LOGIN}/two-factor", headers=CSRF, json={"code": code})
        assert logged.status_code == 200 and logged.json()["user"]["two_factor_enabled"] is True
        assert logged.json()["user"]["recovery_codes_left"] == 10
        client.cookies.clear()

        client.post(LOGIN, headers=CSRF, json=password)
        again = client.post(f"{LOGIN}/two-factor", headers=CSRF, json={"recovery_code": codes[0]})
        assert again.status_code == 200 and again.json()["user"]["recovery_codes_left"] == 9


def test_the_archive_holds_the_secret_and_only_hashes_of_the_recovery_codes(
    live: Settings,
) -> None:
    """What `docs/backup-format.md` warns about: whoever has the archive has every user's
    secret. The recovery codes themselves are nowhere."""
    secret, codes = enrolled(live)

    archive = make_backup(live.db_path, live.manual_backup_dir).path

    with zipfile.ZipFile(archive) as zf:
        database = zf.read("db/app.sqlite")
        excluded = zf.read("manifest.json").decode()
    assert secret.encode() in database
    assert recovery.hash_code(codes[0]).encode() in database
    for code in codes:
        assert code.encode() not in database and code.replace("-", "").encode() not in database
    assert "two_factor" not in excluded and "recovery_codes" not in excluded  # not left out
