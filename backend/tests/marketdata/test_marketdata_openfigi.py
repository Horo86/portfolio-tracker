"""OpenFIGI classifier against synthetic fixtures (invented ISINs, tickers, names and FIGIs);
throttle timing on a fake clock (D-136)."""

import json

import pytest
from fakes import FakeTransport, fixture_response, json_response, queue
from fakes import timeout_error as timeout

from portfolio_tracker.marketdata.base import ErrorCode, Identifier, PriceClass, ProviderError
from portfolio_tracker.marketdata.http import HttpResponse
from portfolio_tracker.marketdata.openfigi import (
    BASE_URL,
    CAPABILITIES,
    HINT_MICS,
    MAX_JOBS,
    OpenFigiClassifier,
)
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy

ISIN = "ZZ0000000418"  # invented ("ZZ" is no country)
NONE = {"warning": "No identifier found."}


class FakeTime:
    def __init__(self) -> None:
        self.now, self.sleeps = 0.0, []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def classifier(*responses: HttpResponse | Exception, interval: float = 0.0):
    time = FakeTime()
    transport = FakeTransport(queue(*responses))
    throttle = Throttle(ThrottlePolicy(min_interval=interval), time.clock, time.sleep)
    return OpenFigiClassifier(transport, throttle), transport, time


def item(sector: str, ticker: str = "XMPW", security_type: str = "ETP") -> dict:
    return {"ticker": ticker, "marketSector": sector, "securityType": security_type}


def answer(everything: list[dict] | None, **per_mic: list[dict]) -> HttpResponse:
    first = {"data": everything} if everything is not None else NONE
    rest = [{"data": per_mic[mic]} if mic in per_mic else NONE for mic in HINT_MICS]
    return json_response([first, *rest])


# --- the request ----------------------------------------------------------------------------


def test_one_request_per_isin_with_every_job() -> None:
    openfigi, transport, _ = classifier(fixture_response("openfigi_mapping_etf.json"))
    openfigi.classify(ISIN)

    (request,) = transport.calls
    assert request.url == f"{BASE_URL}/v3/mapping"
    assert request.headers == {"Content-Type": "application/json"}
    assert request.params == {}
    jobs = json.loads(request.body or b"")
    assert len(jobs) == MAX_JOBS == len(HINT_MICS) + 1  # the keyless maximum, in one request
    assert jobs[0] == {"idType": "ID_ISIN", "idValue": ISIN}
    assert [job["micCode"] for job in jobs[1:]] == list(HINT_MICS)
    assert {job["idValue"] for job in jobs} == {ISIN}  # nothing but the ISIN and an exchange


def test_the_capabilities() -> None:
    assert CAPABILITIES.roles == {"CLASSIFY"}
    assert CAPABILITIES.echoes_isin and CAPABILITIES.official
    assert not CAPABILITIES.needs_credential and not CAPABILITIES.units


@pytest.mark.parametrize(
    "isin", ["", "zz0000000418", "ZZ000000041", "ZZ00000004180", "ZZ-0000004181"]
)
def test_a_malformed_isin_sends_nothing(isin: str) -> None:
    openfigi, transport, _ = classifier()
    assert openfigi.classify(isin) is None
    assert transport.calls == []


# --- the answer -----------------------------------------------------------------------------


def test_an_etf_gets_its_class_and_hints_per_exchange() -> None:
    openfigi, _, _ = classifier(fixture_response("openfigi_mapping_etf.json"))
    facts = openfigi.classify(ISIN)

    assert facts is not None
    assert (facts.isin, facts.price_class) == (ISIN, PriceClass.ETF_STOCK)
    name = "EXAMPLE WORLD EQUITY UCITS ETF"
    assert facts.identifiers[:2] == (
        Identifier("XMPWM", "MTAA", "ETP", name),
        Identifier("XMPW", "XETR", "ETP", name),
    )
    assert [(h.exchange, h.ticker) for h in facts.identifiers] == [
        ("MTAA", "XMPWM"),
        ("XETR", "XMPW"),
        ("XLON", "XMPL"),  # the real line first: shorter tickers come first
        ("XLON", "XMPWEUR"),  # alternative lines after it, each ticker once
        ("XLON", "XMPWGBP"),
        ("XLON", "19999EUR"),
        ("XLON", "19999GBX"),
        ("XAMS", "XMPW"),
    ]


def test_a_bond_is_classified_and_keeps_its_description_as_ticker() -> None:
    openfigi, _, _ = classifier(fixture_response("openfigi_mapping_bond.json"))
    facts = openfigi.classify(ISIN)

    assert facts is not None and facts.price_class is PriceClass.BOND
    assert facts.identifiers == (
        Identifier("EXGOV 1.5 01/02/35", "MTAA", "EURO-ZONE", "EXAMPLE REPUBLIC BOND"),
    )  # a description, not a symbol: the price engine decides what it can use


@pytest.mark.parametrize("name", ["unknown", "invalid_isin"])
def test_an_isin_openfigi_does_not_know_is_none(name: str) -> None:
    openfigi, _, _ = classifier(fixture_response(f"openfigi_mapping_{name}.json"))
    assert openfigi.classify(ISIN) is None


@pytest.mark.parametrize(
    ("sectors", "price_class"),
    [
        (["Equity"], PriceClass.ETF_STOCK),
        (["Govt"], PriceClass.BOND),
        (["Corp", "Corp"], PriceClass.BOND),
        (["Muni"], PriceClass.BOND),
        (["Pfd"], None),  # another sector: no class
        (["Index"], None),
        (["Equity", "Corp"], None),  # results that disagree: no class
        (["Govt", "Corp"], PriceClass.BOND),  # two kinds of bond agree on the class
    ],
)
def test_the_class_comes_from_the_market_sector(
    sectors: list[str], price_class: PriceClass | None
) -> None:
    openfigi, _, _ = classifier(answer([item(sector) for sector in sectors]))
    facts = openfigi.classify(ISIN)
    assert facts is not None and facts.price_class is price_class


def test_hints_without_a_class_are_still_returned() -> None:
    openfigi, _, _ = classifier(answer(None, XETR=[item("Equity", "XMPW")]))
    facts = openfigi.classify(ISIN)
    assert facts is not None and facts.price_class is None
    assert facts.identifiers == (Identifier("XMPW", "XETR", "ETP"),)


@pytest.mark.parametrize(
    "body",
    [
        {"data": []},  # not a list of answers
        [NONE] * (MAX_JOBS - 1),  # one answer missing
        [{"data": "x"}] + [NONE] * (MAX_JOBS - 1),
        [{"result": []}] + [NONE] * (MAX_JOBS - 1),
        [{"data": [{"ticker": "X", "marketSector": 7}]}] + [NONE] * (MAX_JOBS - 1),
        [NONE, {"data": [{"ticker": 7}]}] + [NONE] * (MAX_JOBS - 2),
        ["x"] * MAX_JOBS,
    ],
)
def test_a_changed_format_is_detected(body: object) -> None:
    openfigi, _, _ = classifier(json_response(body))
    with pytest.raises(ProviderError) as error:
        openfigi.classify(ISIN)
    assert error.value.code is ErrorCode.FORMAT_CHANGED


# --- errors and the throttle ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (json_response({}, status=429, Retry_After="30"), ErrorCode.RATE_LIMITED),
        (json_response({}, status=413), ErrorCode.HTTP_ERROR),
        (json_response({}, status=503), ErrorCode.HTTP_ERROR),
        (timeout(), ErrorCode.NETWORK),
        (HttpResponse(200, {}, b"<html>"), ErrorCode.FORMAT_CHANGED),
    ],
)
def test_errors_keep_their_codes(response: HttpResponse | Exception, code: ErrorCode) -> None:
    openfigi, _, _ = classifier(response)
    with pytest.raises(ProviderError) as error:
        openfigi.classify(ISIN)
    assert error.value.code is code


def test_a_429_pauses_the_next_request_for_its_retry_after() -> None:
    openfigi, _, time = classifier(
        json_response({}, status=429, Retry_After="30"),
        fixture_response("openfigi_mapping_etf.json"),
        interval=2.5,
    )
    with pytest.raises(ProviderError):
        openfigi.classify(ISIN)
    assert openfigi.classify(ISIN) is not None
    assert time.sleeps == [30.0]


def test_requests_are_spaced_to_24_a_minute() -> None:
    openfigi, _, time = classifier(
        *[fixture_response("openfigi_mapping_unknown.json")] * 3, interval=2.5
    )
    for _ in range(3):
        openfigi.classify(ISIN)
    assert time.sleeps == [2.5, 2.5]  # the first request goes at once
