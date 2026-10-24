"""Every user learns from `/me` whether market data is on, so the screens never promise
automatic prices an installation does not fetch (D-145)."""

from fastapi.testclient import TestClient
from helpers import PASSWORD, create_admin, investor


def test_an_investor_reads_that_market_data_is_on(on: TestClient) -> None:
    assert on.get("/api/v1/me", headers=investor(on, "bob")).json()["market_data"] == "ON"


def test_with_market_data_off_me_and_the_login_say_so(client: TestClient) -> None:
    create_admin(client)
    assert client.get("/api/v1/me").json()["market_data"] == "OFF"
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["market_data"] == "OFF"
