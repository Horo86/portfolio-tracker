"""D-164 (3): `PT_TRUSTED_PROXIES`. Only when the direct peer is a trusted proxy are
`X-Forwarded-For` (the rightmost hop that is not a trusted proxy) and `X-Forwarded-Proto`
used; otherwise the client is the direct peer, as before (D-82 (6)).

Addresses come from the documentation ranges (RFC 5737, RFC 3849) and private networks."""

import asyncio
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import CSRF, PASSWORD, create_admin, details
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from portfolio_tracker.__main__ import main
from portfolio_tracker.api.forwarded import ForwardedHeaders, parse_trusted_proxies, resolve_client
from portfolio_tracker.config import Settings
from portfolio_tracker.db.models import AuditEntry
from portfolio_tracker.main import create_app

PROXY = "10.0.0.5"  # the reverse proxy
CLIENT = "203.0.113.7"  # a client on the Internet
OTHER = "198.51.100.9"  # another client, or a direct peer that is not a proxy
SPOOF = "192.0.2.66"  # what a client writes in X-Forwarded-For to pass for someone else
TRUSTED = parse_trusted_proxies("10.0.0.0/24, fd00::/8")


def headers(*pairs: tuple[str, str]) -> Headers:
    return Headers(raw=[(k.lower().encode(), v.encode()) for k, v in pairs])


def xff(*values: str) -> Headers:
    return headers(*(("X-Forwarded-For", v) for v in values))


# --- the setting -----------------------------------------------------------------------------


def test_the_setting_is_a_comma_separated_list_of_addresses_and_networks() -> None:
    networks = parse_trusted_proxies(" 10.0.0.1, 172.18.0.0/16 ,::1, fd00::/8")
    assert [str(n) for n in networks] == ["10.0.0.1/32", "172.18.0.0/16", "::1/128", "fd00::/8"]


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_trusts_nobody(text: str) -> None:
    assert parse_trusted_proxies(text) == ()
    assert Settings(trusted_proxies=text).trusted_networks == ()


def test_the_default_trusts_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PT_TRUSTED_PROXIES", raising=False)
    assert Settings().trusted_networks == ()


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("proxy.local", "'proxy.local' is not an IP address or a network"),
        ("10.0.0.300", "'10.0.0.300' is not an IP address or a network"),
        ("10.0.0.0/33", "'10.0.0.0/33' is not an IP address or a network"),
        ("10.0.0.1:8080", "'10.0.0.1:8080' is not an IP address or a network"),
        ("10.0.0.1,,10.0.0.2", "an empty entry"),
        ("10.0.0.1,", "an empty entry"),
        ("0.0.0.0/0", "'0.0.0.0/0' would trust every address"),
        ("::/0", "'::/0' would trust every address"),
    ],
)
def test_an_invalid_entry_fails_fast_with_a_clear_message(text: str, said: str) -> None:
    with pytest.raises(ValueError, match="PT_TRUSTED_PROXIES") as error:
        parse_trusted_proxies(text)
    assert said in str(error.value)
    with pytest.raises(ValidationError, match="PT_TRUSTED_PROXIES"):
        Settings(trusted_proxies=text)


def test_the_server_does_not_start_with_an_invalid_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("PT_TRUSTED_PROXIES", "10.0.0.1, my-proxy")
    started = []
    monkeypatch.setattr("portfolio_tracker.__main__.serve", lambda s: started.append(s))

    assert main([]) == 2

    assert started == []
    err = capsys.readouterr().err
    assert "PT_TRUSTED_PROXIES" in err and "'my-proxy' is not an IP address" in err
    assert "Traceback" not in err


# --- the one function: resolve_client ------------------------------------------------------


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        (xff(CLIENT), CLIENT),
        (xff(f"{SPOOF}, {CLIENT}"), CLIENT),  # a spoofed leftmost value is not used
        (xff(f"{SPOOF}, {CLIENT}, 10.0.0.9"), CLIENT),  # two trusted proxies in a row
        (xff(SPOOF, f"{CLIENT}, 10.0.0.9"), CLIENT),  # several header lines are one list
        (xff(f"garbage, {CLIENT}"), CLIENT),  # malformed to the left of the client: unread
        (xff("2001:db8::7"), "2001:db8::7"),
        (xff("2001:DB8:0::7"), "2001:db8::7"),  # normalised
        (xff("::ffff:203.0.113.7"), CLIENT),  # an IPv4-mapped IPv6 address
        (xff("10.0.0.7, 10.0.0.8"), "10.0.0.7"),  # only proxies: the leftmost of them
        (headers(), PROXY),  # no header: the proxy itself
    ],
)
def test_from_a_trusted_peer_the_rightmost_untrusted_hop_is_the_client(
    forwarded: Headers, expected: str
) -> None:
    assert resolve_client(PROXY, "http", forwarded, TRUSTED).ip == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        ",",
        f"{CLIENT},",  # an empty rightmost hop
        f"{CLIENT}, , 10.0.0.9",  # an empty hop reached before the client
        "garbage",
        "unknown",
        f"{CLIENT}:4711",  # a port
        "[2001:db8::7]:443",
        "[2001:db8::7]",
        "203.0.113.300",
        "x" * 5000,
        "éé",
    ],
)
def test_a_malformed_header_falls_back_to_the_peer(value: str) -> None:
    assert resolve_client(PROXY, "http", xff(value), TRUSTED).ip == PROXY


@pytest.mark.parametrize("peer", [OTHER, "10.0.1.5", "testclient", "", None])
def test_from_an_untrusted_peer_the_headers_are_ignored(peer: str | None) -> None:
    forwarded = headers(("X-Forwarded-For", CLIENT), ("X-Forwarded-Proto", "https"))
    client = resolve_client(peer, "http", forwarded, TRUSTED)
    assert (client.ip, client.scheme) == (peer, "http")


def test_with_nobody_trusted_the_headers_are_ignored_even_from_a_private_peer() -> None:
    forwarded = headers(("X-Forwarded-For", CLIENT), ("X-Forwarded-Proto", "https"))
    client = resolve_client(PROXY, "http", forwarded, ())
    assert (client.ip, client.scheme) == (PROXY, "http")


def test_an_ipv6_or_ipv4_mapped_peer_is_matched_against_the_list() -> None:
    assert resolve_client("fd00::1", "http", xff(CLIENT), TRUSTED).ip == CLIENT
    assert resolve_client("::ffff:10.0.0.5", "http", xff(CLIENT), TRUSTED).ip == CLIENT


@pytest.mark.parametrize(
    ("value", "expected"),
    [("https", "https"), (" HTTPS ", "https"), ("http", "http"), ("ftp", "http"),
     ("https, http", "http"), ("", "http")],
)  # fmt: skip
def test_the_forwarded_scheme_is_used_only_from_a_trusted_peer(value: str, expected: str) -> None:
    proto = headers(("X-Forwarded-Proto", value))
    assert resolve_client(PROXY, "http", proto, TRUSTED).scheme == expected
    assert resolve_client(OTHER, "http", proto, TRUSTED).scheme == "http"


def test_the_middleware_rewrites_the_scope_in_place_so_the_access_log_agrees() -> None:
    """Uvicorn's access log reads the same scope dictionary after the app has run."""
    seen = {}

    async def app(scope, receive, send):
        seen.update(client=scope["client"], scheme=scope["scheme"])

    scope = {
        "type": "http", "scheme": "http", "client": (PROXY, 41000), "path": "/",
        "headers": [(b"x-forwarded-for", f"{SPOOF}, {CLIENT}".encode()),
                    (b"x-forwarded-proto", b"https")],
    }  # fmt: skip
    asyncio.run(ForwardedHeaders(app, trusted=TRUSTED)(scope, None, None))

    assert seen == {"client": (CLIENT, 0), "scheme": "https"}
    assert scope["client"] == (CLIENT, 0) and scope["scheme"] == "https"


# --- through the app: throttle, audit, cookie ------------------------------------------------


def app_with(settings: Settings, fast_passwords, clock, proxies: str = "") -> FastAPI:
    update = {"trusted_proxies": proxies} if proxies else {}
    return create_app(settings.model_copy(update=update), passwords=fast_passwords, clock=clock)


def peer(app: FastAPI, address: str, base_url: str = "https://testserver") -> TestClient:
    return TestClient(app, base_url=base_url, client=(address, 50000))


def fail_login(client: TestClient, username: str, forwarded: str | None = None) -> int:
    extra = {"X-Forwarded-For": forwarded} if forwarded else {}
    response = client.post(
        "/api/v1/auth/login",
        headers=CSRF | extra,
        json={"username": username, "password": "not the password"},
    )
    return response.status_code


def ok_login(client: TestClient, forwarded: str | None = None) -> int:
    extra = {"X-Forwarded-For": forwarded} if forwarded else {}
    response = client.post(
        "/api/v1/auth/login",
        headers=CSRF | extra,
        json={"username": "admin", "password": PASSWORD},
    )
    return response.status_code


IP_THRESHOLD = 30  # D-82


@pytest.mark.parametrize("proxies", ["", "10.0.0.0/24"])
def test_an_untrusted_client_cannot_dodge_the_ip_block_with_forwarded_for(
    settings, fast_passwords, clock, proxies: str
) -> None:
    app = app_with(settings, fast_passwords, clock, proxies)
    with peer(app, OTHER) as attacker:
        for n in range(IP_THRESHOLD):  # a new username each time: only the IP key counts
            assert fail_login(attacker, f"user{n}", forwarded=f"192.0.2.{n + 1}") == 401
        assert fail_login(attacker, "user-next", forwarded="192.0.2.200") == 429


@pytest.mark.parametrize("proxies", ["", "10.0.0.0/24"])
def test_an_untrusted_client_cannot_pin_the_block_on_another_address(
    settings, fast_passwords, clock, proxies: str
) -> None:
    app = app_with(settings, fast_passwords, clock, proxies)
    with peer(app, CLIENT) as victim:
        create_admin(victim)
    with peer(app, OTHER) as attacker:
        for n in range(IP_THRESHOLD + 1):
            fail_login(attacker, f"user{n}", forwarded=CLIENT)
    with peer(app, CLIENT) as victim:
        assert ok_login(victim) == 200


def test_behind_a_trusted_proxy_each_client_has_its_own_ip_block(
    settings, fast_passwords, clock
) -> None:
    app = app_with(settings, fast_passwords, clock, "10.0.0.0/24")
    with peer(app, PROXY) as proxy:
        create_admin(proxy)
        for n in range(IP_THRESHOLD):
            assert fail_login(proxy, f"user{n}", forwarded=f"{SPOOF}, {OTHER}") == 401
        assert fail_login(proxy, "user-next", forwarded=OTHER) == 429  # that client is blocked
        assert fail_login(proxy, "user-next", forwarded=f"{OTHER}, {CLIENT}") == 401
        assert ok_login(proxy, forwarded=CLIENT) == 200  # the others behind the proxy are not


def audit_ips(app: FastAPI) -> list[str | None]:
    with Session(app.state.engine) as db:
        return sorted(db.scalars(select(AuditEntry.ip)))  # one frozen clock: no order


def test_the_audit_ip_is_the_resolved_client(settings, fast_passwords, clock) -> None:
    app = app_with(settings, fast_passwords, clock, "10.0.0.0/24")
    with peer(app, PROXY) as proxy:
        proxy.post(
            "/api/v1/setup/admin",
            headers=CSRF | {"X-Forwarded-For": f"{SPOOF}, {CLIENT}"},
            json={"username": "admin", "password": PASSWORD, "language": "en"} | details("admin"),
        )
        fail_login(proxy, "admin", forwarded=OTHER)
    with peer(app, OTHER) as direct:
        fail_login(direct, "admin", forwarded=SPOOF)

    assert audit_ips(app) == sorted([CLIENT, OTHER, OTHER])


def test_without_the_setting_nothing_changes(settings, fast_passwords, clock, caplog) -> None:
    """No proxy, no variable: the direct peer, as before, whatever the headers say."""
    app = app_with(settings, fast_passwords, clock)
    with peer(app, PROXY) as client:
        create_admin(client)
        logging.getLogger().addHandler(caplog.handler)
        fail_login(client, "admin", forwarded=CLIENT)
    assert audit_ips(app) == [PROXY, PROXY]
    failed = [r for r in caplog.records if r.getMessage() == "login failed"]
    assert [r.ip for r in failed] == [PROXY]  # the log line too


def cookie_warnings(
    app: FastAPI, address: str, proto: str | None, caplog: pytest.LogCaptureFixture
) -> tuple[int, str]:
    """How many 'plain http' warnings a login logs, and its Set-Cookie."""
    extra = {"X-Forwarded-Proto": proto} if proto else {}
    with peer(app, address, base_url="http://portfolio.example") as client:
        logging.getLogger().addHandler(caplog.handler)
        response = client.post(
            "/api/v1/setup/admin",
            headers=CSRF | extra,
            json={"username": "admin", "password": PASSWORD, "language": "en"} | details("admin"),
        )
    warnings = [r for r in caplog.records if "PT_COOKIE_SECURE" in r.getMessage()]
    return len(warnings), response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("address", "proto", "warned"),
    [
        (PROXY, "https", 0),  # TLS at the trusted proxy: no false warning
        (PROXY, "http", 1),
        (PROXY, None, 1),
        (OTHER, "https", 1),  # an untrusted peer cannot silence it
    ],
)
def test_the_plain_http_warning_follows_the_trusted_scheme(
    settings, fast_passwords, clock, caplog, address: str, proto: str | None, warned: int
) -> None:
    app = app_with(settings, fast_passwords, clock, "10.0.0.0/24")
    count, set_cookie = cookie_warnings(app, address, proto, caplog)
    assert count == warned
    assert "Secure" in set_cookie  # the flag is PT_COOKIE_SECURE's, never a header's


# --- the warning for forwarded headers from an untrusted peer (D-175 (5)) --------------------


def forwarded_warnings(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    return [line for line in lines if "PT_TRUSTED_PROXIES" in line["message"]]


@pytest.mark.parametrize("proxies", ["", "10.0.0.0/24"])
@pytest.mark.parametrize("header", [("X-Forwarded-For", CLIENT), ("X-Forwarded-Proto", "https")])
def test_forwarded_headers_from_an_untrusted_peer_are_logged_once_per_start(
    settings, fast_passwords, clock, capsys, proxies: str, header: tuple[str, str]
) -> None:
    app = app_with(settings, fast_passwords, clock, proxies)
    with peer(app, OTHER) as client:
        for _ in range(3):
            client.get("/api/health", headers=dict([header]))
    [warning] = forwarded_warnings(capsys)
    assert warning["level"] == "WARNING"
    # No address and no header value: neither the peer's nor the forwarded one.
    assert not any(a in json.dumps(warning) for a in (OTHER, CLIENT, "https"))
    with peer(app_with(settings, fast_passwords, clock, proxies), OTHER) as again:
        again.get("/api/health", headers=dict([header]))  # a new start warns again
    assert len(forwarded_warnings(capsys)) == 1


def test_no_warning_without_forwarded_headers_or_from_a_trusted_proxy(
    settings, fast_passwords, clock, capsys
) -> None:
    app = app_with(settings, fast_passwords, clock, "10.0.0.0/24")
    with peer(app, OTHER) as direct:
        direct.get("/api/health")
    with peer(app, PROXY) as proxy:
        proxy.get("/api/health", headers={"X-Forwarded-For": CLIENT, "X-Forwarded-Proto": "https"})
    assert forwarded_warnings(capsys) == []
