"""Frankfurter FX adapter: ECB reference rates, no key (D-16, ADR-0007).

Verified against the Frankfurter documentation (https://frankfurter.dev, October 2026) and
its public responses:

- API v2 (v1 is deprecated but kept); public instance `https://api.frankfurter.dev`, also
  self-hostable with Docker (`PT_FX_URL` points to either).
- `GET /v2/rates?providers=ecb&base=EUR&quotes=USD&from=YYYY-MM-DD&to=YYYY-MM-DD` returns a
  JSON array of `{"date", "base", "quote", "rate"}`, one row per published day: no rows on
  weekends and ECB holidays (the engine carries the last rate forward, §12).
- `providers=ecb` keeps the ECB reference rates only (without it, rates are blended across
  providers). ECB data starts on 1999-01-04.
- A currency without data in the range is simply absent; a code Frankfurter does not know is
  an HTTP 422 `{"status", "message"}`. The documentation states no numeric rate limit and no
  maximum date range; it asks clients to cache daily data (we store it, D-122).

One request per currency, so an unknown code affects only that currency, and each series can
start from its own last stored day.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from portfolio_tracker.marketdata.base import ErrorCode, ProviderError, Rate
from portfolio_tracker.marketdata.http import Transport, check_status, parse_json
from portfolio_tracker.marketdata.throttle import Throttle, ThrottlePolicy

NAME = "frankfurter"
DEFAULT_URL = "https://api.frankfurter.dev"
FX_BASE = "EUR"


def _format_changed(detail: str) -> ProviderError:
    return ProviderError(ErrorCode.FORMAT_CHANGED, detail)


def _parse_rows(body: object, currency: str, start: date, end: date) -> list[Rate]:
    if not isinstance(body, list):
        raise _format_changed("expected a JSON array of rates")
    rates: list[Rate] = []
    for row in body:
        if not isinstance(row, dict):
            raise _format_changed("a rate row is not an object")
        try:
            day = date.fromisoformat(row["date"])
            base, quote, rate = row["base"], row["quote"], row["rate"]
        except (KeyError, TypeError, ValueError):
            raise _format_changed("a rate row lacks date, base, quote or rate") from None
        if base != FX_BASE or quote != currency:
            raise _format_changed("a rate row has another base or quote")
        if isinstance(rate, bool) or not isinstance(rate, int | Decimal):
            raise _format_changed("a rate is not a number")
        rate = Decimal(rate)
        if not rate.is_finite() or rate <= 0:
            raise _format_changed("a rate is not positive")
        if not start <= day <= end:
            raise _format_changed("a rate is outside the requested range")
        rates.append(Rate(day, currency, rate))
    return sorted(rates, key=lambda r: r.day)


class FrankfurterFx:
    name = NAME

    def __init__(
        self,
        transport: Transport,
        base_url: str = DEFAULT_URL,
        throttle: Throttle | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._now = now
        self._transport = transport
        self._url = base_url.rstrip("/") + "/v2/rates"
        self._throttle = throttle or Throttle(ThrottlePolicy(min_interval=1.0))

    @property
    def throttle(self) -> Throttle:
        return self._throttle

    def rates(self, currencies: Sequence[str], start: date, end: date) -> Sequence[Rate]:
        result: list[Rate] = []
        for currency in currencies:
            if currency != FX_BASE:
                result.extend(self._throttle.call(lambda c=currency: self._one(c, start, end)))
        return result

    def _one(self, currency: str, start: date, end: date) -> list[Rate]:
        response = self._transport.fetch(
            self._url,
            {
                "providers": "ecb",
                "base": FX_BASE,
                "quotes": currency,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        if response.status in (404, 422):  # a code Frankfurter does not know: no data
            return []
        check_status(response, self._now())
        return _parse_rows(parse_json(response.body), currency, start, end)
