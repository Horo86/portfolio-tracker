"""B' guard (D-166): an invented `ZZ` ISIN never leaves the process. The real transport
refuses a request carrying one, in the URL, the query, a header or the body, before any
network call; a run with one such security and an ordinary one still updates the ordinary one.

The ordinary ISIN starts with `QZ`, a code ISO 3166 leaves to private use like `ZZ`, so it
belongs to no real security either; only `ZZ` is the guard's mark."""

import io
import json
import logging
from email.message import Message
from urllib.parse import parse_qsl, urlsplit

import pytest
from fakes import FakeTransport, Provider
from fastapi.testclient import TestClient
from helpers import fake_isin
from test_api_reports import ETF, get, post, tx
from test_marketdata_linking_api import link_of
from test_marketdata_plugin_proof import NOW

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError
from portfolio_tracker.marketdata.http import UrllibTransport
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy
from portfolio_tracker.services.scheduler import market_job

ZZ = fake_isin(71)


def isin(prefix: str, serial: int) -> str:
    body = f"{prefix}{serial:09d}"
    digits = "".join(str(int(ch, 36)) for ch in body)
    total = sum(
        (int(ch) * (2 if i % 2 == 0 else 1)) // 10 + (int(ch) * (2 if i % 2 == 0 else 1)) % 10
        for i, ch in enumerate(reversed(digits))
    )
    return body + str((10 - total % 10) % 10)


QZ = isin("QZ", 72)


class Tripwire:
    """The network: any use fails the test."""

    def __init__(self) -> None:
        self.opened = 0

    def open(self, request, timeout=None):
        self.opened += 1
        raise AssertionError("the network was touched")


class _Reply(io.BytesIO):
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in headers.items():
            self.headers[name] = value


class FakeNetwork:
    """The network behind the real transport: each request goes to the fake providers."""

    def __init__(self, answers: Provider) -> None:
        self.fake = FakeTransport(answers)
        self.urls: list[str] = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        parts = urlsplit(request.full_url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
        answer = self.fake.fetch(
            base, dict(parse_qsl(parts.query)), headers=dict(request.header_items()),
            body=request.data,
        )  # fmt: skip
        return _Reply(answer.status, dict(answer.headers), answer.body)


def transport(network) -> UrllibTransport:
    real = UrllibTransport()
    real._opener = network  # the only seam: everything above it is the app's own code
    return real


def refused(network: Tripwire, *args, **kwargs) -> ProviderError:
    with pytest.raises(ProviderError) as error:
        transport(network).fetch(*args, **kwargs)
    assert network.opened == 0
    return error.value


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [
        (("https://query2.finance.yahoo.com/v1/finance/search/" + ZZ,), {}),
        (("https://query2.finance.yahoo.com/v1/finance/search?q=" + ZZ,), {}),
        (("https://query2.finance.yahoo.com/v1/finance/search", {"q": ZZ}), {}),
        (("https://query2.finance.yahoo.com/v1/finance/search", {"q": ZZ.lower()}), {}),
        (("https://api.openfigi.com/v3/mapping",),
         {"body": json.dumps([{"idType": "ID_ISIN", "idValue": ZZ}]).encode()}),
        (("https://example.org/form",), {"body": f"isin={ZZ.lower()}&x=1".encode()}),
        (("https://example.org/lookup",), {"headers": {"X-Lookup": ZZ}}),
    ],
    ids=["path", "query in url", "params", "lowercase", "json body", "form body", "header"],
)  # fmt: skip
def test_the_real_transport_refuses_a_zz_isin_before_the_network(args, kwargs) -> None:
    error = refused(Tripwire(), *args, **kwargs)
    assert error.code is ErrorCode.INVENTED_ISIN
    assert ZZ not in str(error) and ZZ.lower() not in str(error)


def test_an_ordinary_isin_goes_through(caplog: pytest.LogCaptureFixture) -> None:
    network = FakeNetwork(Provider())
    answer = transport(network).fetch("https://query2.finance.yahoo.com/v1/finance/search",
                                      {"q": QZ})  # fmt: skip
    assert answer.status == 200
    assert network.urls == [f"https://query2.finance.yahoo.com/v1/finance/search?q={QZ}"]


def test_a_longer_token_that_merely_contains_zz_is_not_an_isin() -> None:
    network = FakeNetwork(Provider())
    for text in (f"X{ZZ}", f"{ZZ}9", "crumb=aZZ0000000000b"):
        transport(network).fetch("https://query2.finance.yahoo.com/v1/finance/search",
                                 {"q": text})  # fmt: skip
    assert len(network.urls) == 3


def test_a_refusal_is_no_engine_failure() -> None:
    """All development data is `ZZ`: refusals must never pause an engine for the others."""
    throttle = Throttle(ThrottlePolicy(min_interval=0, stop_after_failures=2),
                        clock=lambda: 0.0, sleep=lambda _: None)  # fmt: skip

    def refuse():
        raise ProviderError(ErrorCode.INVENTED_ISIN, "refused")

    for _ in range(5):
        with pytest.raises(ProviderError):
            throttle.call(refuse)
    assert not throttle.exhausted
    assert throttle.call(lambda: "answer") == "answer"


def test_a_run_updates_the_ordinary_security_and_leaves_the_zz_one_not_updated(
    on: TestClient, answers: Provider, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Both securities are linked and priced by one job over the real transport: the `QZ` one
    gets its listing and closes, the `ZZ` one stays without them, and the job goes on."""
    monkeypatch.setattr(on.app.state.market_data.scheduler, "trigger", lambda kind: None)
    account = post(on, "/api/v1/accounts", {"name": "Conto", "currency": "EUR"})
    assets = {}
    for code in (ZZ, QZ):
        assets[code] = post(
            on, "/api/v1/assets", ETF | {"isin": code, "name": f"Esempio {code[:2]}"}
        )
        tx(on, account, "BUY", "2025-06-02", asset_id=assets[code]["id"], quantity="10",
           unit_price="10.00", gross_amount="100.00", total_amount="100.00",
           affects_cash=False)  # fmt: skip
    network = FakeNetwork(answers)
    market_data = on.app.state.market_data

    with caplog.at_level(logging.INFO):
        summary = market_job(
            on.app.state.engine, market_data.registry, transport(network), market_data.cipher,
            urls=market_data.urls, clock=lambda: NOW,
        )("BACKFILL")  # fmt: skip

    assert summary.result in ("OK", "PARTIAL")  # the run went on
    sent = " ".join(network.urls).upper()
    assert ZZ not in sent and QZ in sent  # never left; the ordinary one did
    assert link_of(on, assets[QZ]["id"])[1] == "XMPW.MI"
    assert any("/v8/finance/chart/XMPW.MI" in url for url in network.urls)  # its closes
    assert link_of(on, assets[ZZ]["id"])[1] is None
    positions = {p["isin"]: p for p in get(on, f"/api/v1/accounts/{account['id']}/positions")}
    assert positions[QZ]["price_status"] == "OK"
    assert positions[ZZ]["price_status"] != "OK"  # not updated: valued at cost
    assert positions[ZZ]["estimated_at_cost"] is True
    refusals = [r for r in caplog.records if "invented ISIN" in r.getMessage()]
    assert len(refusals) == 1  # once, without the value
    assert all(ZZ not in f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
