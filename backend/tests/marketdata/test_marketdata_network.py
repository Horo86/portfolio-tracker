"""The network guard of conftest.py and the stdlib HTTP transport, against a local server only
(127.0.0.1). Nothing here leaves the machine."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fakes import FakeTransport, json_response, queue

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError
from portfolio_tracker.marketdata.http import (
    HttpResponse,
    UrllibTransport,
    get_json,
    parse_json,
    parse_retry_after,
    with_query,
)

NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)

# --- The guard ----------------------------------------------------------------------------


def test_guard_refuses_an_outbound_connection() -> None:
    with pytest.raises(RuntimeError, match="must not"):
        socket.create_connection(("192.0.2.1", 443), timeout=1)  # TEST-NET-1, never routed


def test_guard_refuses_a_raw_connect_and_connect_ex() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="must not"):
            sock.connect(("198.51.100.7", 80))
        with pytest.raises(RuntimeError, match="must not"):
            sock.connect_ex(("198.51.100.7", 80))


def test_guard_refuses_dns_lookups() -> None:
    for lookup in (socket.getaddrinfo, socket.gethostbyname, socket.gethostbyname_ex):
        with pytest.raises(RuntimeError, match="must not resolve"):
            lookup("example.com", *([443] if lookup is socket.getaddrinfo else []))


def test_guard_refuses_datagrams() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(RuntimeError, match="must not send"):
            sock.sendto(b"x", ("192.0.2.1", 53))
        with pytest.raises(RuntimeError, match="must not send"):
            sock.sendto(b"x", 0, ("192.0.2.1", 53))
        with pytest.raises(RuntimeError, match="must not send"):
            sock.sendmsg([b"x"], [], 0, ("192.0.2.1", 53))


def test_guard_clears_proxy_variables() -> None:
    import os
    import urllib.request

    assert not any(os.environ.get(n) for n in ("HTTP_PROXY", "https_proxy", "ALL_PROXY"))
    assert urllib.request.getproxies_environment() == {}


def test_guard_stops_the_real_transport_before_it_leaves() -> None:
    with pytest.raises(RuntimeError, match="must not"):
        UrllibTransport().fetch("https://example.com/data", timeout=1)


# --- A local server -----------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}  # noqa: RUF012 - replaced per test

    def do_GET(self) -> None:
        route = self.routes.get(self.path.split("?", 1)[0])
        if route is None:
            self.send_error(404)
            return
        route(self)

    def log_message(self, *_args: object) -> None:  # keep test output clean
        pass


@pytest.fixture
def server() -> Iterator[tuple[str, dict]]:
    routes: dict = {}
    handler = type("Handler", (_Handler,), {"routes": routes})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", routes
    httpd.shutdown()
    httpd.server_close()


def _reply(status: int, body: bytes = b"", **headers: str):
    def route(req: BaseHTTPRequestHandler) -> None:
        req.send_response(status)
        for name, value in headers.items():
            req.send_header(name.replace("_", "-"), value)
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    return route


def test_transport_returns_status_headers_and_body(server: tuple[str, dict]) -> None:
    base, routes = server
    seen: dict = {}

    def echo(req: BaseHTTPRequestHandler) -> None:
        seen["path"] = req.path
        seen["agent"] = req.headers["User-Agent"]
        seen["extra"] = req.headers["X-Test"]
        _reply(200, b'{"ok": true}', X_Seen="yes")(req)

    routes["/data"] = echo
    response = UrllibTransport().fetch(
        f"{base}/data", {"from": "2026-01-01", "q": "a b"}, headers={"X-Test": "1"}
    )
    assert response.status == 200
    assert response.body == b'{"ok": true}'
    assert response.header("X-SEEN") == "yes"
    assert seen == {
        "path": "/data?from=2026-01-01&q=a+b",
        "agent": "portfolio-tracker",
        "extra": "1",
    }


def test_transport_returns_error_statuses_without_raising(server: tuple[str, dict]) -> None:
    base, routes = server
    routes["/busy"] = _reply(429, b"slow down", Retry_After="7")
    response = UrllibTransport().fetch(f"{base}/busy")
    assert (response.status, response.header("retry-after"), response.body) == (
        429,
        "7",
        b"slow down",
    )
    assert UrllibTransport().fetch(f"{base}/missing").status == 404


def test_transport_refuses_a_declared_oversized_body(server: tuple[str, dict]) -> None:
    base, routes = server
    routes["/big"] = _reply(200, b"x" * 101)
    with pytest.raises(ProviderError) as error:
        UrllibTransport().fetch(f"{base}/big", max_bytes=100)
    assert error.value.code is ErrorCode.RESPONSE_TOO_LARGE
    assert len(UrllibTransport().fetch(f"{base}/big", max_bytes=101).body) == 101


def test_transport_caps_a_body_without_length(server: tuple[str, dict]) -> None:
    base, routes = server

    def unbounded(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Connection", "close")
        req.end_headers()
        req.wfile.write(b"y" * 5000)

    routes["/stream"] = unbounded
    with pytest.raises(ProviderError) as error:
        UrllibTransport().fetch(f"{base}/stream", max_bytes=1000)
    assert error.value.code is ErrorCode.RESPONSE_TOO_LARGE


def test_transport_follows_a_redirect_on_the_same_host(server: tuple[str, dict]) -> None:
    base, routes = server
    routes["/old"] = _reply(302, Location="/new")
    routes["/new"] = _reply(200, b"[]")
    assert UrllibTransport().fetch(f"{base}/old").body == b"[]"


@pytest.mark.parametrize(
    "location",
    ["http://192.0.2.1/elsewhere", "http://localhost:1/x", "https://127.0.0.1/x", "file:///etc"],
)
def test_transport_refuses_a_redirect_to_another_origin(
    server: tuple[str, dict], location: str
) -> None:
    base, routes = server
    routes["/jump"] = _reply(302, Location=location)
    with pytest.raises(ProviderError) as error:
        UrllibTransport().fetch(f"{base}/jump")
    assert error.value.code is ErrorCode.REDIRECT_REFUSED
    assert error.value.status == 302


def test_transport_times_out(server: tuple[str, dict]) -> None:
    base, routes = server
    release = threading.Event()

    def slow(req: BaseHTTPRequestHandler) -> None:
        release.wait(5)
        _reply(200, b"late")(req)

    routes["/slow"] = slow
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError) as error:
            UrllibTransport().fetch(f"{base}/slow", timeout=0.2)
    finally:
        release.set()
    assert error.value.code is ErrorCode.NETWORK
    assert time.monotonic() - started < 3


def test_network_errors_never_quote_the_url() -> None:
    with socket.socket() as probe:  # a local port with nothing listening
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(ProviderError) as error:
        UrllibTransport().fetch(f"http://127.0.0.1:{port}/x", {"apikey": "SECRET-123"})
    assert error.value.code is ErrorCode.NETWORK
    assert "SECRET" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_transport_accepts_only_http_urls() -> None:
    with pytest.raises(ValueError, match="http or https"):
        UrllibTransport().fetch("file:///etc/passwd")


def test_with_query_keeps_an_existing_query() -> None:
    assert with_query("https://h/x", None) == "https://h/x"
    assert with_query("https://h/x?a=1", {"b": "2"}) == "https://h/x?a=1&b=2"


# --- JSON over HTTP -----------------------------------------------------------------------


def _get(*responses: HttpResponse | Exception) -> object:
    return get_json(FakeTransport(queue(*responses)), "https://provider.test/x", now=lambda: NOW)


def test_json_numbers_are_exact_decimals() -> None:
    body = _get(HttpResponse(200, {}, b'{"close": 101.10, "n": 3, "tiny": 0.1}'))
    assert body == {"close": Decimal("101.10"), "n": 3, "tiny": Decimal("0.1")}
    assert str(body["close"]) == "101.10"  # type: ignore[index]


@pytest.mark.parametrize(
    "raw", [b"{not json", b'{"x": NaN}', b'{"x": Infinity}', b"\xff\xfe", b"[" * 100_000]
)
def test_unreadable_json_is_a_format_change(raw: bytes) -> None:
    with pytest.raises(ProviderError) as error:
        parse_json(raw)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


def test_rate_limit_carries_retry_after() -> None:
    with pytest.raises(ProviderError) as error:
        _get(json_response({}, status=429, Retry_After="30"))
    assert (error.value.code, error.value.status, error.value.retry_after) == (
        ErrorCode.RATE_LIMITED,
        429,
        30.0,
    )


def test_server_errors_and_not_found() -> None:
    with pytest.raises(ProviderError) as error:
        _get(json_response({}, status=503, Retry_After="5"))
    assert (error.value.code, error.value.status, error.value.retry_after) == (
        ErrorCode.HTTP_ERROR,
        503,
        5.0,
    )
    with pytest.raises(ProviderError) as error:
        _get(json_response({}, status=404))
    assert error.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize(
    ("header", "seconds"),
    [
        (None, None),
        ("", None),
        ("120", 120.0),
        ("Mon, 02 Mar 2026 12:01:30 GMT", 90.0),
        ("Mon, 02 Mar 2026 11:00:00 GMT", 0.0),  # in the past: retry now
        ("soon", None),
        ("-5", None),
    ],
)
def test_parse_retry_after(header: str | None, seconds: float | None) -> None:
    assert parse_retry_after(header, NOW) == seconds


def test_fake_transport_records_requests() -> None:
    transport = FakeTransport(queue(json_response([1])))
    get_json(transport, "https://provider.test/x", {"a": "1"}, timeout=3, now=lambda: NOW)
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert (call.url, call.params, call.timeout) == ("https://provider.test/x", {"a": "1"}, 3)
    assert json.loads(json.dumps(call.params)) == {"a": "1"}


def test_a_dripping_server_hits_the_response_deadline(server: tuple[str, dict]) -> None:
    base, routes = server
    stop = threading.Event()

    def drip(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Content-Length", "100000")
        req.end_headers()
        while not stop.is_set():
            req.wfile.write(b"x")
            req.wfile.flush()
            time.sleep(0.05)

    routes["/drip"] = drip
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError) as error:
            UrllibTransport(deadline=0.5).fetch(f"{base}/drip", timeout=5)
    finally:
        stop.set()
    assert error.value.code is ErrorCode.NETWORK
    assert time.monotonic() - started < 3


def test_odd_digits_in_headers_are_ignored(server: tuple[str, dict]) -> None:
    base, routes = server

    def odd(req: BaseHTTPRequestHandler) -> None:
        req.send_response(200)
        req.send_header("Connection", "close")
        req.end_headers()
        req.wfile.write(b"[]")

    routes["/odd"] = odd
    assert UrllibTransport().fetch(f"{base}/odd").body == b"[]"
    assert parse_retry_after("²", NOW) is None
    assert parse_retry_after("٣", NOW) is None  # Arabic-Indic digit: not ASCII


@pytest.mark.parametrize(
    "raw",
    [b"[1e400]", b"[1e-400]", b"[1e999999999]", b"[" + b"1" * 41 + b"]", b"[0." + b"1" * 41 + b"]"],
)
def test_numbers_out_of_bounds_are_a_format_change(raw: bytes) -> None:
    with pytest.raises(ProviderError) as error:
        parse_json(raw)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


def test_ordinary_numbers_are_within_bounds() -> None:
    assert parse_json(b"[0.000001, 12345678.12345678, 1e10, 7]") == [
        Decimal("0.000001"),
        Decimal("12345678.12345678"),
        Decimal("1e10"),
        7,
    ]
