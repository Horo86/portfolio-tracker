import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from portfolio_tracker import __version__
from portfolio_tracker.build_info import build_date_text, is_preview
from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "database": "ok",
        "build_date": None,  # a development run
        "preview": is_preview(__version__),
    }


def test_health_reports_the_image_build_date_in_utc(
    tmp_path: Path, fast_passwords, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image sets PT_BUILD_DATE to the commit time (D-167); health shows it in UTC."""
    monkeypatch.setenv("PT_BUILD_DATE", "2026-03-05T21:30:00+01:00")
    monkeypatch.setenv("PT_BUILD_COMMIT", "0123456789abcdef0123456789abcdef01234567")
    settings = Settings(data_dir=tmp_path / "data")

    with TestClient(create_app(settings, passwords=fast_passwords)) as client:
        body = client.get("/api/health").json()

    assert body["build_date"] == "2026-03-05T20:30:00Z"
    assert settings.build_commit.startswith("0123456789")


def test_openapi_document_is_built_but_not_served_by_default(client: TestClient) -> None:
    """Off by default (D-163, `PT_API_DOCS`); the document still builds for the route matrix."""
    assert client.get("/api/openapi.json").status_code == 404
    assert "/api/health" in client.app.openapi()["paths"]


@pytest.mark.parametrize(
    ("version", "preview"),
    [
        ("0.9.0", True),
        ("0.0.0", True),  # an uninstalled tree
        ("0.10.3", True),
        ("1.0.0", False),
        ("1.2.3", False),
        ("10.0.1", False),
        ("1.0.0-rc.1", True),
        ("1.0.0rc1", True),
        ("1.0.0.dev1", True),
        ("1.0", True),
        ("", True),
    ],
)
def test_a_preview_is_not_plain_or_has_major_zero(version: str, preview: bool) -> None:
    assert is_preview(version) is preview


def test_build_date_text_is_utc_with_z() -> None:
    assert build_date_text(None) is None
    assert build_date_text(datetime(2026, 3, 5, 20, 30, 1, tzinfo=UTC)) == "2026-03-05T20:30:01Z"


def test_the_build_settings_are_empty_in_a_development_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PT_BUILD_DATE", "")  # what the image's ENV holds in a local build
    settings = Settings(data_dir=tmp_path)
    assert settings.build_date is None and settings.build_commit == ""


@pytest.mark.parametrize("text", ["yesterday", "2026-03-05T20:30:00"])
def test_a_build_date_without_an_offset_or_not_a_date_stops_the_start(text: str) -> None:
    with pytest.raises(ValidationError, match="build_date"):
        Settings(build_date=text)


def test_the_startup_log_names_the_build(
    tmp_path: Path, fast_passwords, capsys: pytest.CaptureFixture
) -> None:
    """The JSON log line on stdout (the app's own handler replaces pytest's)."""
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, passwords=fast_passwords)):
        pass

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    started = [line for line in lines if line["message"] == "application started"]
    assert len(started) == 1
    assert started[0]["version"] == __version__
    assert started[0]["build_date"] == "development build"
