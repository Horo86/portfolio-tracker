"""Test doubles for market data: a fake HTTP transport that serves synthetic responses.

Every response here is invented. Fixture files under `tests/fixtures/marketdata/` follow the
documented shape of the real providers' answers, with fictitious ISINs, symbols and prices.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from portfolio_tracker.marketdata.base import Capabilities, ErrorCode, ProviderError, Rate, Role
from portfolio_tracker.marketdata.http import DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT, HttpResponse

FIXTURES = Path(__file__).parents[3] / "tests/fixtures/marketdata"
FX_CAPABILITIES = Capabilities(roles=frozenset({Role.FX}))


@dataclass(frozen=True, slots=True)
class FakeRequest:
    url: str
    params: Mapping[str, str]
    timeout: float
    headers: Mapping[str, str]
    max_bytes: int
    body: bytes | None = None


Handler = Callable[[FakeRequest], HttpResponse | Exception]


@dataclass
class FakeTransport:
    """Answers each call with `handler(request)`; an exception returned by the handler is
    raised (e.g. a timeout). Records every request in `calls`."""

    handler: Handler
    calls: list[FakeRequest] = field(default_factory=list)

    def fetch(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        body: bytes | None = None,
    ) -> HttpResponse:
        request = FakeRequest(
            url, dict(params or {}), timeout, dict(headers or {}), max_bytes, body
        )
        self.calls.append(request)
        result = self.handler(request)
        if isinstance(result, Exception):
            raise result
        if len(result.body) > max_bytes:
            raise ProviderError(ErrorCode.RESPONSE_TOO_LARGE, "fake body over the cap")
        return result


def queue(*results: HttpResponse | Exception) -> Handler:
    """A handler answering with `results` in order (an error if called once too often)."""
    pending = list(results)

    def handler(_request: FakeRequest) -> HttpResponse | Exception:
        if not pending:
            raise AssertionError("unexpected extra request")
        return pending.pop(0)

    return handler


def _json_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """`Retry_After="5"` → `{"retry-after": "5"}` (names as the transport returns them)."""
    return {
        "content-type": "application/json",
        **{name.lower().replace("_", "-"): value for name, value in headers.items()},
    }


def json_response(payload: object, status: int = 200, **headers: str) -> HttpResponse:
    return HttpResponse(
        status,
        _json_headers(headers),
        json.dumps(payload).encode(),
    )


def fixture_response(name: str, status: int = 200, **headers: str) -> HttpResponse:
    return HttpResponse(
        status,
        _json_headers(headers),
        (FIXTURES / name).read_bytes(),
    )


def timeout_error() -> ProviderError:
    return ProviderError(ErrorCode.NETWORK, "TimeoutError")


@dataclass
class FakeFxProvider:
    """Serves invented rates per currency. `fail` maps a currency to the error it raises;
    `during_fetch` runs inside every call (e.g. to assert that no write lock is held)."""

    series: dict[str, dict[date, Decimal]] = field(default_factory=dict)
    fail: dict[str, ProviderError] = field(default_factory=dict)
    during_fetch: Callable[[], None] | None = None
    calls: list[tuple[tuple[str, ...], date, date]] = field(default_factory=list)
    name: str = "fake-fx"
    capabilities: Capabilities = FX_CAPABILITIES

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        self.calls.append((tuple(currencies), start, end))
        if self.during_fetch:
            self.during_fetch()
        result = []
        for currency in currencies:
            if currency in self.fail:
                raise self.fail[currency]
            for day, rate in sorted(self.series.get(currency, {}).items()):
                if start <= day <= end:
                    result.append(Rate(day, currency, rate))
        return result


CHARTS = {"XMPW.MI": "eur_milan", "XMPW.DE": "eur_etf", "XMPW.L": "gbp_pence"}


class Provider:
    """Answers for any query: the synthetic Yahoo search and charts, OpenFIGI knowing
    nothing, Frankfurter without rates. `during` runs inside each call; `status` makes the
    Yahoo search fail."""

    def __init__(self, unknown: frozenset[str] = frozenset()) -> None:
        self.during = None
        self.calls: list[FakeRequest] = []
        self.searches: list[FakeRequest] = []
        self.status = 200
        self.unknown = unknown  # queries Yahoo finds nothing for

    def __call__(self, request: FakeRequest):
        self.calls.append(request)
        if self.during:
            self.during()
        if request.url.endswith("/v1/finance/search"):
            self.searches.append(request)
            if self.status != 200:
                return json_response({}, status=self.status, Retry_After="30")
            if request.params.get("q") in self.unknown:
                return fixture_response("yahoo_search_empty.json")
            return fixture_response("yahoo_search_isin.json")
        if "/v8/finance/chart/" in request.url:
            name = CHARTS.get(request.url.rsplit("/", 1)[1], "not_found")
            return fixture_response(f"yahoo_chart_{name}.json", 404 if name == "not_found" else 200)
        if "openfigi" in request.url:
            return fixture_response("openfigi_mapping_unknown.json")
        return json_response([])  # Frankfurter: no rates


class InstantTime:
    """The throttles' monotonic clock: a wait advances it at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
