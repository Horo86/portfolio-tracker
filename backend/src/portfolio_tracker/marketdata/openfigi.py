"""OpenFIGI classifier: the price class of an ISIN and its tickers on main exchanges
(D-118, D-131, D-136, ADR-0007). Bloomberg's free, official identifier-mapping API; no key.

Verified on 17 October 2026 against https://www.openfigi.com/api/documentation, the terms of
service and live answers for public ISINs:

- `POST https://api.openfigi.com/v3/mapping` with a JSON array of jobs
  `{"idType": "ID_ISIN", "idValue": ..., "micCode"?: ...}`; the answer is one entry per job,
  in order: `{"data": [...]}`, `{"warning": "No identifier found."}` or `{"error": "..."}`
  (an ISIN with an unknown country, such as the `ZZ` ones of the tests, is the error
  "Invalid idValue format."; a MIC the filter refuses, such as `XMIL`, or an unknown MIC gets
  the warning, checked on 18 October 2026). Any other job error fails the request.
- Each result has `ticker`, `name`, `exchCode` (Bloomberg's own code, never interpreted
  here), `securityType` (`ETP`, `Common Stock`, ...), `marketSector` (`Equity`, `Govt`,
  `Corp`, ...) and FIGIs; no ISIN and no MIC come back.
- Without a key: 25 requests a minute and 10 jobs per request; 429 carries `retry-after`.

Choices (D-136):
- One request per ISIN with 10 jobs: one without filter gives the price class, and one per
  MIC of `HINT_MICS` gives that exchange's tickers as hints. The lookup is exact by ISIN, so
  its answers stand for that ISIN (`echoes_isin`).
- Price class from `marketSector`: `Equity` → ETF/stock; `Govt`, `Corp`, `Muni` → bond; any
  other sector, or results that disagree → no class.
- Hints keep the ticker as OpenFIGI writes it (a bond's "ticker" is a description); the price
  engine decides what it can use. Within one MIC, shorter tickers come first: alternative
  trading lines (seen on London: `19618EUR`) carry longer codes.
"""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from portfolio_tracker.marketdata.base import (
    Capabilities,
    ErrorCode,
    Identifier,
    PriceClass,
    ProviderError,
    Role,
    SecurityFacts,
)
from portfolio_tracker.marketdata.http import Transport, check_status, parse_json
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy

NAME = "openfigi"
BASE_URL = "https://api.openfigi.com"
MAX_JOBS = 10  # per request without a key
# Exchanges asked for tickers, in order (ADR-0007 table): ISO 10383 MICs that the mapping
# filter accepts and that Yahoo serves. One more job (no filter) gives the class.
HINT_MICS = ("MTAA", "XETR", "XLON", "XAMS", "XPAR", "XSWX", "XMAD", "XBRU", "XWBO")
THROTTLE = ThrottlePolicy(min_interval=2.5)  # 24 requests a minute, under the 25 allowed
CAPABILITIES = Capabilities(
    roles=frozenset({Role.CLASSIFY}),
    echoes_isin=True,
    terms_url="https://www.openfigi.com/docs/terms-of-service",
)
_SECTORS = {
    "Equity": PriceClass.ETF_STOCK,
    "Govt": PriceClass.BOND,
    "Corp": PriceClass.BOND,
    "Muni": PriceClass.BOND,
}
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_MAX_TEXT = 128
# The job error for an identifier OpenFIGI cannot map (e.g. an ISIN with an unknown country),
# as seen in October 2026. A MIC the filter refuses answers "No identifier found." instead.
_UNKNOWN_IDENTIFIER = frozenset({"invalid idvalue format."})


def _changed(detail: str) -> ProviderError:
    return ProviderError(ErrorCode.FORMAT_CHANGED, detail)


def _text(item: dict[str, Any], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _changed(f"{key} is not a string")
    value = value.strip()
    return value[:_MAX_TEXT] or None


def _results(entry: object) -> list[dict[str, Any]]:
    """The results of one job; empty for "No identifier found." (also the answer for a MIC
    the filter does not accept) and for an identifier OpenFIGI refuses as invalid. Any other
    job error is not understood: the whole answer counts as failed, so the instrument waits
    for the next run instead of being taken as unknown (D-133)."""
    if not isinstance(entry, dict):
        raise _changed("a job answer is not an object")
    if "data" in entry:
        data = entry["data"]
        if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
            raise _changed("job data is not a list of objects")
        return data
    if isinstance(entry.get("warning"), str):
        return []
    error = entry.get("error")
    if isinstance(error, str):
        if error.strip().casefold() in _UNKNOWN_IDENTIFIER:
            return []
        raise _changed("a job error that is not an invalid identifier")
    raise _changed("a job answer has neither data, warning nor error")


def _price_class(results: list[dict[str, Any]]) -> PriceClass | None:
    classes = {_SECTORS.get(_text(item, "marketSector") or "") for item in results}
    if len(classes) != 1:
        return None  # no results, or sectors that disagree
    return classes.pop()  # None for any other sector


def _hints(mic: str, results: list[dict[str, Any]]) -> list[Identifier]:
    by_ticker: dict[str, Identifier] = {}
    for item in results:
        ticker = _text(item, "ticker")
        if ticker and ticker not in by_ticker:
            by_ticker[ticker] = Identifier(
                ticker, mic, _text(item, "securityType"), _text(item, "name")
            )
    return sorted(by_ticker.values(), key=lambda hint: (len(hint.ticker), hint.ticker))


class OpenFigiClassifier:
    name = NAME
    capabilities = CAPABILITIES

    def __init__(
        self,
        transport: Transport,
        throttle: Throttle | None = None,
        base_url: str = BASE_URL,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._transport = transport
        self._throttle = throttle or Throttle(THROTTLE)
        self._url = base_url.rstrip("/") + "/v3/mapping"
        self._now = now

    @property
    def throttle(self) -> Throttle:
        return self._throttle

    def classify(self, isin: str) -> SecurityFacts | None:
        """The price class and the hints of `isin`; None when OpenFIGI does not know it."""
        if not _ISIN.fullmatch(isin):
            return None
        jobs = [{"idType": "ID_ISIN", "idValue": isin}]
        jobs += [{"idType": "ID_ISIN", "idValue": isin, "micCode": mic} for mic in HINT_MICS]
        body = json.dumps(jobs).encode()

        def call() -> object:
            response = self._transport.fetch(
                self._url, body=body, headers={"Content-Type": "application/json"}
            )
            check_status(response, self._now())
            return parse_json(response.body)

        answer = self._throttle.call(call)
        if not isinstance(answer, list) or len(answer) != len(jobs):
            raise _changed("expected one answer per job")
        everything, *per_mic = (_results(entry) for entry in answer)
        hints = [
            hint
            for mic, results in zip(HINT_MICS, per_mic, strict=True)
            for hint in _hints(mic, results)
        ]
        if not everything and not hints:
            return None
        return SecurityFacts(isin, _price_class(everything), tuple(hints))
