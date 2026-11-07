"""The administrator's System section (D-168 (6)): the schema, the last upgrade and where its
copy is, the kept copies and the latest manual backup. File names relative to the data folder
and sizes only: never an absolute path, data or figures."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from helpers import create_admin, investor

from portfolio_tracker import __version__
from portfolio_tracker.backup import make_backup

V1 = "/api/v1"


def system(client: TestClient, headers=None) -> dict:
    response = client.get(f"{V1}/admin/system", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_fresh_instance_has_no_upgrade_no_copy_and_no_backup(client: TestClient) -> None:
    create_admin(client)

    assert system(client) == {
        "version": __version__,
        "schema_revision": "0006",
        "schema_head": "0006",
        "last_upgrade": None,
        "pre_migration_copies": [],
        "latest_backup": None,
    }


def test_the_newest_recorded_upgrade_and_every_kept_copy_are_shown(client: TestClient) -> None:
    create_admin(client)
    folder = client.app.state.settings.pre_migration_backup_dir
    folder.mkdir(parents=True)
    (folder / "app-0004-20260101T000000Z.sqlite").write_bytes(b"x" * 10)  # made by 0.9.0
    newer = folder / "app-0005-20260301T101500.123456Z.sqlite"
    newer.write_bytes(b"y" * 20)
    newer.with_suffix(".json").write_text(json.dumps({
        "from": "0005", "to": "0006", "version": "1.0.0", "at": "2026-03-01T10:15:00Z",
        "size": 20, "copy": newer.name, "status": "done"}))  # fmt: skip
    (folder / "notes.txt").write_text("not a copy")
    (folder / "app-by-hand.sqlite").write_text("not a copy either")

    shown = system(client)

    assert shown["last_upgrade"] == {
        "from_revision": "0005", "to_revision": "0006", "version": "1.0.0",
        "at": "2026-03-01T10:15:00Z", "status": "done",
        "copy_path": f"backups/pre-migration/{newer.name}",
    }  # fmt: skip
    assert shown["pre_migration_copies"] == [
        {"path": f"backups/pre-migration/{newer.name}", "size": 20,
         "at": "2026-03-01T10:15:00Z", "status": "done"},
        {"path": "backups/pre-migration/app-0004-20260101T000000Z.sqlite", "size": 10,
         "at": "2026-01-01T00:00:00Z", "status": None},
    ]  # fmt: skip


def test_the_latest_manual_backup_is_shown_by_its_relative_name(client: TestClient) -> None:
    create_admin(client)
    settings = client.app.state.settings
    first = make_backup(settings.db_path, settings.manual_backup_dir)
    older = settings.manual_backup_dir / "portfolio-0.9.0-20200101T000000Z.zip"
    older.write_bytes(b"old")
    (settings.manual_backup_dir / "something-else.zip").write_bytes(b"not ours")

    shown = system(client)["latest_backup"]

    assert shown == {
        "path": f"backups/manual/{first.path.name}",
        "size": first.size,
        "at": shown["at"],
    }
    assert shown["at"].endswith("Z") and len(shown["at"]) == 20


def test_no_absolute_path_is_in_the_answer(client: TestClient) -> None:
    create_admin(client)
    settings = client.app.state.settings
    make_backup(settings.db_path, settings.manual_backup_dir)
    folder = settings.pre_migration_backup_dir
    folder.mkdir(parents=True)
    (folder / "app-0005-20260301T101500Z.sqlite").write_bytes(b"y")

    text = client.get(f"{V1}/admin/system").text

    assert str(settings.data_dir) not in text and str(Path.home()) not in text


def test_an_investor_gets_404(client: TestClient) -> None:
    create_admin(client)
    bob = investor(client, "bob")
    client.cookies.clear()

    response = client.get(f"{V1}/admin/system", headers=bob)

    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"


def test_the_start_log_names_the_schema_revision(app, capsys) -> None:
    """The app logs JSON to standard output (its own handler replaces pytest's)."""
    with TestClient(app, base_url="https://testserver"):
        pass
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    [started] = [line for line in lines if line["message"] == "application started"]
    assert started["schema_revision"] == "0006"
