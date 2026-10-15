from fastapi import FastAPI
from fastapi.testclient import TestClient

from portfolio_tracker.api.errors import ApiError, install_error_handlers


def make_client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom() -> None:
        raise ApiError(409, "QUANTITY_CONFLICT", "quantity below zero", transaction_id="t1")

    @app.get("/typed/{number}")
    def typed(number: int) -> dict[str, int]:
        return {"number": number}

    return TestClient(app)


def test_api_error_body_has_code_detail_and_extra() -> None:
    response = make_client().get("/boom")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "quantity below zero",
        "code": "QUANTITY_CONFLICT",
        "transaction_id": "t1",
    }


def test_unknown_route_uses_the_same_body() -> None:
    response = make_client().get("/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found", "code": "NOT_FOUND"}


def test_request_validation_error_uses_the_same_body() -> None:
    response = make_client().get("/typed/abc")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "REQUEST_INVALID"
    assert body["errors"][0]["loc"] == ["path", "number"]
