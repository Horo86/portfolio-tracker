from fastapi.testclient import TestClient

from portfolio_tracker import __version__


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__, "database": "ok"}


def test_openapi_document_is_built_but_not_served_by_default(client: TestClient) -> None:
    """Off by default (D-163, `PT_API_DOCS`); the document still builds for the route matrix."""
    assert client.get("/api/openapi.json").status_code == 404
    assert "/api/health" in client.app.openapi()["paths"]
