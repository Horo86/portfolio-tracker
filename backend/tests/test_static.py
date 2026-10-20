from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)")
    settings = Settings(data_dir=tmp_path / "data", static_dir=static, market_data=False)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_frontend_is_served_with_spa_fallback_but_not_for_api(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert "<title>app</title>" in client.get("/some/client/route").text
    assert client.get("/api/unknown").status_code == 404


def test_index_and_fallback_are_revalidated_on_every_use(client: TestClient) -> None:
    for path in ("/", "/index.html", "/accounts", "/icon.svg"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "no-cache", path


def test_hashed_assets_are_cached_for_good(client: TestClient) -> None:
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE


def test_a_revalidated_asset_keeps_its_cache_header(client: TestClient) -> None:
    first = client.get("/assets/index-abc123.js")
    again = client.get("/assets/index-abc123.js", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == IMMUTABLE
    index = client.get("/")
    again = client.get("/", headers={"If-None-Match": index.headers["etag"]})
    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/assets/missing.js", "/assets/x/y.css", "/assets", "/assets/"])
def test_a_missing_asset_is_404_never_the_page(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert "<title>app</title>" not in response.text
    assert "text/html" not in response.headers.get("content-type", "")
