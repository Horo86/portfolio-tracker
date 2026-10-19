"""Reference lists for the screens (M5). Invented users only."""

from fastapi.testclient import TestClient
from helpers import create_admin

from portfolio_tracker.currencies import ISO_4217


def test_currencies_are_the_validated_list_sorted(client: TestClient) -> None:
    create_admin(client)
    response = client.get("/api/v1/currencies")
    assert response.status_code == 200
    codes = response.json()
    assert codes == sorted(ISO_4217)
    assert {"EUR", "USD", "CHF", "GBP"} <= set(codes)


def test_currencies_need_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/currencies")
    assert response.status_code == 401
    assert response.json()["code"] == "NOT_AUTHENTICATED"
