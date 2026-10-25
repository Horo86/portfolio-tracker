"""Request bodies are bounded before they are read (D-101, D-114): a declared size over the
limit is refused without reading, a chunked body as soon as it passes the limit. The import
uploads have their own bound (and a file over 10 MB once decoded gets the same code); every
other route, also before authentication, has 4 MiB. Invented data only."""

import asyncio
import base64
import json

import pytest
from fastapi.testclient import TestClient
from helpers import CSRF, create_admin

from portfolio_tracker.api.limits import MAX_IMPORT_BODY, MAX_REQUEST_BODY, bound_for
from portfolio_tracker.importers.base import MAX_FILE_BYTES

PREVIEW = "/api/v1/imports/preview"
CHUNK = 64 * 1024


def call(app, path: str, headers: list[tuple[bytes, bytes]], chunks: int) -> dict:
    """Drive the app at the ASGI level: `chunks` body parts of 64 KiB are offered, and the
    result says how many the app pulled and what it answered."""
    pulled = 0
    sent: list[dict] = []

    async def receive() -> dict:
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b"x" * CHUNK, "more_body": pulled < chunks}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": path, "raw_path": path.encode(), "root_path": "",
        "query_string": b"", "headers": [(b"host", b"testserver"), *headers],
        "client": ("203.0.113.9", 50000), "server": ("testserver", 443),
    }  # fmt: skip
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return {"status": start["status"], "body": json.loads(body), "pulled": pulled}


@pytest.mark.parametrize("path", [PREVIEW, "/api/v1/imports/confirm"])
def test_a_declared_size_over_the_limit_is_refused_unread(client: TestClient, path) -> None:
    length = str(MAX_IMPORT_BODY + 1).encode()
    result = call(client.app, path, [(b"content-length", length)], chunks=1000)
    assert (result["status"], result["body"]["code"]) == (413, "IMPORT_TOO_LARGE")
    assert result["pulled"] == 0  # not a byte of the body was read


def test_a_chunked_body_is_refused_as_soon_as_it_passes_the_limit(client: TestClient) -> None:
    result = call(client.app, PREVIEW, [(b"content-type", b"application/json")], chunks=1000)
    assert (result["status"], result["body"]["code"]) == (413, "IMPORT_TOO_LARGE")
    assert result["pulled"] == MAX_IMPORT_BODY // CHUNK + 1  # far fewer than offered


def test_an_oversized_body_is_refused_before_authentication(client: TestClient) -> None:
    body = b'{"content_base64": "' + b"A" * MAX_IMPORT_BODY + b'"}'
    response = client.post(PREVIEW, content=body, headers={"content-type": "application/json"})
    assert (response.status_code, response.json()["code"]) == (413, "IMPORT_TOO_LARGE")


def test_a_file_over_10_mb_once_decoded_has_the_same_code(client: TestClient) -> None:
    create_admin(client)
    encoded = base64.b64encode(b"x" * (MAX_FILE_BYTES + 1)).decode()
    response = client.post(PREVIEW, headers=CSRF, json={"content_base64": encoded})
    assert (response.status_code, response.json()["code"]) == (413, "IMPORT_TOO_LARGE")


def test_a_body_within_the_limit_reaches_the_route(client: TestClient) -> None:
    response = client.post(PREVIEW, json={"content_base64": "eA=="})
    assert response.status_code == 401  # read, then refused for the missing session


def test_the_import_uploads_have_their_own_bound() -> None:
    assert (bound_for(PREVIEW).limit, bound_for(PREVIEW).code) == (MAX_IMPORT_BODY,
                                                                    "IMPORT_TOO_LARGE")  # fmt: skip
    assert bound_for("/sub/path" + PREVIEW).limit == MAX_IMPORT_BODY  # served under a prefix
    assert (bound_for("/api/v1/transactions").limit, bound_for("/x").code) == (
        MAX_REQUEST_BODY, "REQUEST_TOO_LARGE")  # fmt: skip
    assert MAX_IMPORT_BODY < 2 * MAX_FILE_BYTES  # base64 (4/3) plus small JSON overhead


@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/login", "/api/v1/setup/admin", "/api/v1/transactions/bulk-edit",
     "/api/v1/transactions/batch/preview", "/api/v1/transactions/batch"],
)  # fmt: skip
def test_every_other_route_is_bounded_before_authentication(client: TestClient, path) -> None:
    length = str(MAX_REQUEST_BODY + 1).encode()
    result = call(client.app, path, [(b"content-length", length)], chunks=1000)
    assert (result["status"], result["body"]["code"], result["pulled"]) == (
        413, "REQUEST_TOO_LARGE", 0)  # fmt: skip
    result = call(client.app, path, [(b"content-type", b"application/json")], chunks=1000)
    assert (result["status"], result["body"]["code"]) == (413, "REQUEST_TOO_LARGE")
    assert result["pulled"] == MAX_REQUEST_BODY // CHUNK + 1


def test_a_large_legitimate_bulk_edit_fits(client: TestClient) -> None:
    """1,000 edits with notes of 2,000 characters: about 2.5 MB, under the 4 MiB bound."""
    edit = {"id": "00000000-0000-4000-8000-000000000001", "notes": "n" * 2000,
            "quantity": "1.00000000", "gross_amount": "10.00", "total_amount": "10.00"}  # fmt: skip
    body = {"edits": [edit] * 1000}
    response = client.post("/api/v1/transactions/bulk-edit", json=body)
    assert response.status_code == 401  # read whole, then refused for the missing session


@pytest.mark.parametrize(
    "path", ["/api/v1/transactions/batch/preview", "/api/v1/transactions/batch"]
)
def test_the_largest_batch_fits(client: TestClient, path) -> None:
    """50 rows with the longest ASCII notes and metadata the fields accept: well under the
    bound (D-147), so the batch routes need no bound of their own. Metadata is measured
    unescaped, so a client that writes other characters as `\\u` escapes sends more; past
    the bound it gets the 413 of every path."""
    row = {"account_id": "00000000-0000-4000-8000-000000000001", "type": "BUY",
           "trade_date": "2025-12-31", "quantity": "1", "unit_price": "10",
           "notes": "n" * 2000, "metadata": {"k": "m" * 9_980}}  # fmt: skip
    body = {"transactions": [row] * 50}
    assert len(json.dumps(body)) < MAX_REQUEST_BODY // 4
    response = client.post(path, json=body)
    assert response.status_code == 401  # read whole, then refused for the missing session
