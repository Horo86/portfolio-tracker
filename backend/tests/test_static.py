from pathlib import Path

from fastapi.testclient import TestClient

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app


def test_frontend_is_served_with_spa_fallback_but_not_for_api(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    settings = Settings(data_dir=tmp_path / "data", static_dir=static)

    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 200
        assert "<title>app</title>" in client.get("/some/client/route").text
        assert client.get("/api/unknown").status_code == 404
