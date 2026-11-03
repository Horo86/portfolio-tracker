"""D-163: CSRF on setup and login (R-15), security headers on every response (R-16), the API
docs off by default, and a 500 handler that logs only the exception type and the route."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, create_admin

from portfolio_tracker.config import Settings
from portfolio_tracker.main import create_app

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)
HEADERS = {
    "content-security-policy": CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
}
SECRET = "secret-7f3a holding 1234.56 EUR"


def assert_security_headers(response) -> None:
    for name, value in HEADERS.items():
        assert response.headers.get(name) == value, (name, response.request.url)
    assert "strict-transport-security" not in response.headers  # the proxy's job (D-163)


@pytest.fixture
def web(tmp_path: Path, fast_passwords) -> Iterator[TestClient]:
    """The app with a built frontend and a route that fails with a recognizable message."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>app</title>")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)")
    settings = Settings(data_dir=tmp_path / "data", static_dir=static, market_data=False)
    app = create_app(settings, passwords=fast_passwords)

    @app.get("/api/v1/test-boom/{item_id}")
    def boom(item_id: str) -> None:
        raise RuntimeError(f"{SECRET} {item_id}")

    app.router.routes.insert(0, app.router.routes.pop())  # before the frontend's catch-all

    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client


# --- R-15: CSRF on setup and login ---------------------------------------------------------


def test_setup_without_the_csrf_header_is_refused(client: TestClient) -> None:
    body = {"username": "admin", "password": PASSWORD, "language": "en"}
    refused = client.post("/api/v1/setup/admin", json=body)
    assert refused.status_code == 403
    assert refused.json()["code"] == "CSRF_CHECK_FAILED"
    assert client.get("/api/v1/setup/status").json()["setup_required"] is True
    assert client.post("/api/v1/setup/admin", json=body, headers=CSRF).status_code == 201


@pytest.mark.parametrize("issue_token", [False, True])  # cookie or bearer: every client
def test_login_without_the_csrf_header_is_refused(client: TestClient, issue_token: bool) -> None:
    create_admin(client)
    client.cookies.clear()
    body = {"username": "admin", "password": PASSWORD, "issue_token": issue_token}
    refused = client.post("/api/v1/auth/login", json=body)
    assert refused.status_code == 403
    assert refused.json()["code"] == "CSRF_CHECK_FAILED"
    assert "set-cookie" not in refused.headers
    assert client.post("/api/v1/auth/login", json=body, headers=CSRF).status_code == 200


# --- R-16: the headers on every response ---------------------------------------------------


def test_the_security_headers_are_on_every_kind_of_response(web: TestClient) -> None:
    responses = {
        "api": web.get("/api/health"),
        "api error 401": web.get("/api/v1/me"),
        "api error 404": web.get("/api/v1/no-such-route"),
        "api error 422": web.post("/api/v1/setup/admin", json={}, headers=CSRF),
        "index": web.get("/"),
        "spa fallback": web.get("/accounts"),
        "asset": web.get("/assets/index-abc123.js"),
        "missing asset": web.get("/assets/missing-000.js"),
        "500": web.get("/api/v1/test-boom/42"),
    }
    expected = {"api error 401": 401, "api error 404": 404, "api error 422": 422,
                "missing asset": 404, "500": 500}  # fmt: skip
    for name, response in responses.items():
        assert response.status_code == expected.get(name, 200), name
        assert_security_headers(response)


def test_the_csp_is_exactly_the_decided_one(web: TestClient) -> None:
    assert web.get("/").headers["content-security-policy"] == CSP
    assert "upgrade-insecure-requests" not in CSP  # plain http on localhost keeps working


# --- The API docs: off by default ----------------------------------------------------------


def test_the_api_docs_are_off_by_default(web: TestClient) -> None:
    for path in ("/api/docs", "/api/openapi.json", "/api/docs/oauth2-redirect"):
        response = web.get(path)
        assert response.status_code == 404, path
        assert "swagger" not in response.text.lower()
        assert response.headers["content-type"].startswith("application/json")


def test_the_api_docs_are_served_with_pt_api_docs(tmp_path: Path, fast_passwords) -> None:
    settings = Settings(data_dir=tmp_path / "data", api_docs=True, market_data=False)
    with TestClient(create_app(settings, passwords=fast_passwords)) as client:
        assert "/api/health" in client.get("/api/openapi.json").json()["paths"]
        docs = client.get("/api/docs")
        assert docs.status_code == 200 and "swagger" in docs.text.lower()
        # The interactive page loads Swagger UI from a CDN: a relaxed policy on that path only.
        assert docs.headers["content-security-policy"] != CSP
        assert "frame-ancestors 'none'" in docs.headers["content-security-policy"]
        assert client.get("/api/health").headers["content-security-policy"] == CSP


def test_pt_api_docs_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PT_API_DOCS", "true")
    assert Settings().api_docs is True
    monkeypatch.delenv("PT_API_DOCS")
    assert Settings().api_docs is False


# --- Unexpected errors ---------------------------------------------------------------------


def test_an_unexpected_error_is_logged_by_type_and_route_only(
    web: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = web.get("/api/v1/test-boom/item-99")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error", "code": "INTERNAL_ERROR"}
    assert SECRET not in response.text and "item-99" not in response.text
    server = [r for r in caplog.records if not r.name.startswith("httpx")]  # not the client's
    logged = "\n".join(
        f"{r.getMessage()} {r.__dict__} {r.exc_text or ''} {r.exc_info or ''}" for r in server
    )
    assert SECRET not in logged
    assert "item-99" not in logged  # the path's values neither: the route template only
    errors = [r for r in server if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is None
    assert errors[0].__dict__.get("exception") == "RuntimeError"
    assert errors[0].__dict__.get("route") == "/api/v1/test-boom/{item_id}"
