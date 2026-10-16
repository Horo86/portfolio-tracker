"""Imports through the API (M3; D-100, D-101, D-113): the synthetic Directa fixture end to end,
re-import, the "do not move cash" option with re-selection, stale previews, errors and owner
isolation. Synthetic files and invented data only."""

import base64
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin, investor

FIXTURES = Path(__file__).parents[2] / "tests/fixtures/directa"
XLSX = (FIXTURES / "directa_synthetic_2024.xlsx").read_bytes()
EXPECTED = json.loads((FIXTURES / "directa_synthetic_2024.expected.json").read_text("utf-8"))
V1 = "/api/v1"
CHOICES = {"isin:IE0TEST00020": {"asset_type": "ETF"}, "isin:IT0TEST00015": {"asset_type": "STOCK"}}
BOND_ORDER = "order:T0000000000004"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def post(client: TestClient, path: str, body: dict, expected: int = 200, headers=CSRF) -> dict:
    response = client.post(f"{V1}{path}", headers=headers, json=body)
    assert response.status_code == expected, response.text
    return response.json()


def get(client: TestClient, path: str, expected: int = 200, headers=None):
    response = client.get(f"{V1}{path}", headers=headers)
    assert response.status_code == expected, response.text
    return response.json()


def directa_body(account_id: str | None, **extra) -> dict:
    body = {"content_base64": b64(XLSX), "file_name": "directa.xlsx", "assets": CHOICES}
    if account_id:
        body["account_id"] = account_id
    return body | extra


def preview_and_confirm(client: TestClient, body: dict, selected=None, expected=201) -> dict:
    seen = post(client, "/imports/preview", body)
    chosen = (
        selected if selected is not None else [x["key"] for x in seen["lines"] if x["selected"]]
    )
    confirm = body | {"fingerprint": seen["fingerprint"], "selected": chosen}
    return post(client, "/imports/confirm", confirm, expected)


@pytest.fixture
def account(client: TestClient) -> dict:
    create_admin(client)  # cookie session: writes carry the CSRF header
    return post(client, "/accounts", {"name": "Conto titoli", "currency": "EUR"}, 201)


def test_directa_fixture_end_to_end(client: TestClient, account) -> None:
    """M3 "Done when": the fixture through the API; a re-import imports nothing."""
    seen = post(client, "/imports/preview", directa_body(account["id"]))
    assert (seen["format"], seen["rows_read"], seen["account_code"]) == (
        "directa-xlsx", EXPECTED["rows_in_file"], EXPECTED["account_code"])  # fmt: skip
    assert seen["counts"] == {"selected": 11, "to_import": 11, "to_review": 1,
                              "skipped_by_choice": 0, "duplicates": 0, "not_recognized": 1,
                              "ambiguous": 0, "invalid": 0}  # fmt: skip
    review = next(x for x in seen["lines"] if x["key"] == BOND_ORDER)
    assert (review["category"], review["selectable"], review["selected"]) == (
        "TO_REVIEW",
        True,
        False,
    )
    assert review["reasons"] == [{"code": "UNCONFIRMED_MOVEMENT_TYPE", "params": {
        "original_type": "Rit.credito disaggio", "mapped_as": "TAX_CREDIT"}}]  # fmt: skip
    assert isinstance(review["gross_amount"], str)  # decimal strings (ADR-0004)
    assert [(w["code"], w["date"]) for w in seen["warnings"]] == [
        (w["code"], w["date"]) for w in EXPECTED["default_selection"]["warnings"]
    ]

    done = preview_and_confirm(client, directa_body(account["id"]))
    assert len(done["transactions"]) == 11 and len(done["assets_created"]) == 3
    (batch,) = done["batches"]
    assert (batch["imported"], batch["to_review_skipped"], batch["not_recognized"]) == (11, 1, 1)
    assert batch["transactions"] == 11 and batch["format"] == "directa-xlsx"
    summary = get(client, f"/accounts/{account['id']}/summary")
    assert Decimal(summary["cash"]) == Decimal(EXPECTED["default_selection"]["final_cash"])
    assert get(client, f"/imports/{batch['id']}") == batch
    assert [b["id"] for b in get(client, "/imports")] == [batch["id"]]
    assert get(client, f"/accounts/{account['id']}")["external_code"] == "B0000"

    again = post(client, "/imports/preview", directa_body(None, assets={}))  # remembered code
    assert again["counts"]["selected"] == 0 and again["counts"]["duplicates"] == 11
    redone = preview_and_confirm(client, directa_body(None, assets={}))
    assert redone["transactions"] == [] and redone["batches"][0]["duplicates"] == 11
    assert len(get(client, "/imports")) == 2
    assert get(client, f"/accounts/{account['id']}/summary")["cash"] == summary["cash"]


def test_do_not_move_cash_with_reselection(client: TestClient, account) -> None:
    body = directa_body(account["id"], move_cash=False)
    seen = post(client, "/imports/preview", body)
    expected = EXPECTED["do_not_move_cash_option"]
    assert seen["counts"]["skipped_by_choice"] == len(expected["skipped_by_default_seq"])
    chosen = [x["key"] for x in seen["lines"] if x["selected"]] + [BOND_ORDER, "protocol:90000001"]
    reseen = post(client, "/imports/preview", body | {"selected": chosen})
    assert reseen["fingerprint"] == seen["fingerprint"] and reseen["counts"]["selected"] == 9
    done = preview_and_confirm(client, body, selected=chosen)
    assert len(done["transactions"]) == expected["transactions_imported"] + 1
    assert done["batches"][0]["move_cash"] is False
    cash = get(client, f"/accounts/{account['id']}/summary")["cash"]
    assert Decimal(cash) == Decimal("5000.00")  # only the deposit selected again moved cash


def test_stale_preview_is_refused(client: TestClient, account) -> None:
    seen = post(client, "/imports/preview", directa_body(account["id"]))
    deposit = {"account_id": account["id"], "type": "DEPOSIT", "trade_date": "2024-02-01",
               "total_amount": "1.00"}  # fmt: skip
    post(client, "/transactions", deposit, 201)
    chosen = [x["key"] for x in seen["lines"] if x["selected"]]
    body = directa_body(account["id"]) | {"fingerprint": seen["fingerprint"], "selected": chosen}
    refused = post(client, "/imports/confirm", body, 409)
    assert refused["code"] == "IMPORT_PREVIEW_STALE"
    assert get(client, "/imports") == []


def test_confirmation_needs_the_choices(client: TestClient, account) -> None:
    body = directa_body(account["id"], assets={})
    seen = post(client, "/imports/preview", body)
    assert [b["code"] for b in seen["blocking"]] == ["ASSET_TYPE_REQUIRED"] * 2
    chosen = [x["key"] for x in seen["lines"] if x["selected"]]
    confirm = body | {"fingerprint": seen["fingerprint"], "selected": chosen}
    refused = post(client, "/imports/confirm", confirm, 422)
    assert refused["code"] == "IMPORT_NOT_READY"
    assert refused["blocking"][0] == {"code": "ASSET_TYPE_REQUIRED", "asset": "isin:IE0TEST00020"}
    assert get(client, "/imports") == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"content_base64": "not base64!"}, "IMPORT_FILE_INVALID"),
        ({"content_base64": b64(b"x,y\n1,2\n")}, "IMPORT_FILE_INVALID"),
        ({"content_base64": b64(b"x"), "format": "other"}, "IMPORT_FORMAT_UNKNOWN"),
        ({"content_base64": b64(b"format_version,account,trade_date,type\n2,M,2024-01-01,"
                                 b"DEPOSIT\n")}, "IMPORT_FORMAT_VERSION"),
        ({"content_base64": b64(XLSX)}, "IMPORT_ACCOUNT_REQUIRED"),
    ],
)  # fmt: skip
def test_file_and_input_errors(client: TestClient, account, body, code) -> None:
    refused = post(client, "/imports/preview", body, 422)
    assert refused["code"] == code


def test_choice_and_selection_errors(client: TestClient, account) -> None:
    usd = post(client, "/accounts", {"name": "Dollars", "currency": "USD"}, 201)
    assert post(client, "/imports/preview", directa_body(usd["id"]), 422)["code"] == (
        "IMPORT_ACCOUNT_CURRENCY")  # fmt: skip
    wrong = directa_body(account["id"], selected=["line:999"])
    assert post(client, "/imports/preview", wrong, 422)["code"] == "IMPORT_SELECTION_INVALID"
    mapped = directa_body(account["id"], assets={"isin:IT0TEST00015": {"asset_id": usd["id"]}})
    assert post(client, "/imports/preview", mapped, 422)["code"] == "IMPORT_CHOICE_INVALID"
    unknown = directa_body(account["id"], unknown_field=1)
    assert post(client, "/imports/preview", unknown, 422)["code"] == "REQUEST_INVALID"


def test_csv_with_two_accounts_through_the_api(client: TestClient, account) -> None:
    csv = ("account,account_currency,trade_date,type,total_amount\n"
           "Main,EUR,2024-01-02,DEPOSIT,100.00\n"
           "Dollars,USD,2024-01-03,DEPOSIT,50.00\n")  # fmt: skip
    body = {"content_base64": b64(csv.encode()), "file_name": "export.csv"}
    seen = post(client, "/imports/preview", body)
    assert [(a["key"], a["new"], a["currency"]) for a in seen["accounts"]] == [
        ("Main", True, "EUR"), ("Dollars", True, "USD")]  # fmt: skip
    done = preview_and_confirm(client, body)
    assert len(done["batches"]) == 2 and len(done["accounts_created"]) == 2
    names = {a["name"]: a for a in get(client, "/accounts")}
    assert names["Dollars"]["currency"] == "USD" and names["Dollars"]["transaction_count"] == 1
    listed = get(client, f"/imports?account_id={names['Dollars']['id']}")
    assert [b["account_id"] for b in listed] == [names["Dollars"]["id"]]


def test_another_users_imports_are_not_found(client: TestClient, account) -> None:
    batch = preview_and_confirm(client, directa_body(account["id"]))["batches"][0]
    bob = investor(client, "bob")
    client.cookies.clear()
    assert get(client, f"/imports/{batch['id']}", 404, bob)["code"] == "NOT_FOUND"
    assert get(client, f"/imports?account_id={account['id']}", 404, bob)["code"] == "NOT_FOUND"
    assert get(client, "/imports", headers=bob) == []
    refused = post(client, "/imports/preview", directa_body(account["id"]), 404, bob)
    assert refused["code"] == "NOT_FOUND"
    # Bob's own import of the same file is his: nothing of Alice's is a duplicate for him.
    mine = post(client, "/accounts", {"name": "Bob", "currency": "EUR"}, 201, bob)
    seen = post(client, "/imports/preview", directa_body(mine["id"]), headers=bob)
    assert seen["counts"]["duplicates"] == 0 and all(a["new"] for a in seen["assets"])


def test_cookie_writes_need_the_csrf_header(client: TestClient, account) -> None:
    response = client.post(f"{V1}/imports/preview", json=directa_body(account["id"]))
    assert (response.status_code, response.json()["code"]) == (403, "CSRF_CHECK_FAILED")
